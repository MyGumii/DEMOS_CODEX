# Notion parity xhigh 개발 인계 및 전체 실행 계획

기준 시각: 2026-08-14 Asia/Seoul의 shared tree  
대상: 이후 이 작업을 넘겨받는 xhigh 개발 에이전트와 리뷰어  
문서 상태: 현재 shared tree를 기준으로 한 실행 인계본. 제품 parity 완료 선언, 운영 적용 승인, live migration 승인이 아니다.  
현재 판단: NP-2A substrate 약 80%, 전체 계획의 engineering progress 약 10%, 공식 differential parity 0%

> **SUPERSEDED EXECUTION PRIORITY — 2026-08-14:** 이 문서의 schema/checkpoint 역사와 안전 불변식은
> 보존하지만, 현재 제품 범위·진행률 분모·다음 구현 slice는
> `docs/NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`와 최신 UI-first checkpoint를 따른다.
> 아래 NP2C-5a Business/Enterprise Archive 지시는 optional paid Archive lane의 historical next step일 뿐
> Core UI를 막는 전역 gate가 아니다. xhigh의 다음 Core slice는 실제 browser evidence 기반
> `UI1-FREE-DESKTOP-GOLDEN-JOURNEY`다. 이 문서의 오래된 중간 runtime snapshot이나 10% 추정치를
> current truth로 사용하지 않는다.

권장 실행 프로필은 `gpt-5.6-sol`의 `xhigh` reasoning이다. 이 문서는 xhigh가 ultra 단계의 긴 대화 문맥을 추측으로 복원하지 않고도 `현재 사실 확인 → 파일 소유권 확보 → 한 vertical slice 구현 → 증거 고정` 순서로 진행하도록 만든 harness다. 모델 변경 자체는 안전하지만 이 문서의 재감사·중단 gate를 생략할 권한을 주지는 않는다.

### 2026-08-14 historical optional NP2C-5a authorization-inspection flow checkpoint

이 항목이 아래 NP2C-5a live-authorization preflight/evidence-acceptance, NP2C-5와 4e 이하 모든 historical checkpoint보다 최신이다. 재개 시 machine contract v19, matrix v1, runbook, authorization/acceptance verifiers와 `docs/notion_parity/checkpoints/np2c5a-authorization-inspection-flow-20260814.json`을 먼저 확인하고 Archive schema나 runtime을 이미 구현된 것으로 추정하지 않는다.

- current workspace runtime은 계속 `2026081317`이며 1317 frozen checksum, actual schema hash와 179개 object는 4d/4e checkpoint 그대로다. NP2C-5는 source 또는 catalog DDL을 바꾸지 않았다.
- official Archive는 legacy `pages.archived`, `archive_page`, historical `action='archive'`가 나타내는 Trash/Recycle Bin compatibility와 별도 상태다. 설계상 Archive page는 binding과 locator projection을 `active`로 유지하고, physical tree lifecycle도 `active`로 유지하되 canonical lifecycle만 `archived`가 된다.
- source lineage는 current v3 `2026081303` 다음에 H5e가 예약한 미구현 v4 `2026081304`를 보존한다. Archive는 v4를 건너뛰거나 재사용하지 않고 exact successor candidate v5 `2026081305`에서만 시작할 수 있다. v4 또는 v5는 이 slice에서 구현되지 않았다.
- candidate workspace `2026081318`은 archived canonical state, visibility/readiness guard와 atomic archive finalizer를 위한 예약일 뿐 DDL·migration·hash가 없다. Current/candidate version을 같은 것으로 표시하거나 1317 아래에서 의미를 바꾸면 안 된다.
- 공식 reference가 고정한 것은 Business/Enterprise beta와 edit-permission gate, archived content 접근 유지, parent archive의 subpage 영향, default search/sidebar/database-view 비노출, 기존 workspace link 유지다. Nested unarchive, create/move/duplicate/Trash/restore/public-link/IAM 세부는 live characterization 전까지 unknown이다.
- machine contract v19 status는 `official_archive_source_contract_designed_source_v5_and_workspace_1318_blocked_default_off`다. `official_archive_unarchive_contract_designed=true` 외의 source implementation, 1318 implementation, catalog finalizer/rehearsal, runtime, route/UI, resolver, live migration과 purge는 false다.
- NP2C-5 design의 historical hash-bound evidence는 `docs/notion_parity/checkpoints/np2c5-official-archive-design-20260814.json`에 있고 preparation, evidence-acceptance, live-authorization preflight snapshots은 각각 `np2c5a-live-characterization-preparation-20260814.json`, `np2c5a-evidence-acceptance-harness-20260814.json`, `np2c5a-live-authorization-preflight-20260814.json`에 있다. 최신 snapshot은 위 authorization-inspection flow checkpoint다. 최신 체크포인트가 current document/source hashes와 맞지 않으면 cached plan을 버리고 read-only audit부터 다시 한다.
- NP2C-5a preparation harness는 별도 matrix v1, strict verifier/adversarial tests, runbook, non-authorizing external record inspection, ≤24h external authorization preflight와 external evidence-bundle acceptance verifier로 준비됐다. `--inspect-only`는 exact record와 redaction을 검사해 canonical digest 후보를 표시하지만 `valid=false`, characterization authority=false, digest match=false를 유지한다. 그 digest를 사용자가 별도 채널에서 명시 승인한 뒤에만 current-time preflight가 Business/Enterprise synthetic tenants, independent operator/reviewer와 public/plan-transition risk approval을 확인한다. Record mutation은 이전 digest 승인을 무효화한다. Acceptance verifier는 같은 승인 digest 아래 exact 46 required pairs의 distinct path와 exact fixture/action/manifest/result/redaction bytes 및 canonical decision을 결속한다. 상태는 `prepared_not_authorized_not_executed`, actual user approval·live execution·accepted evidence·completion·source/catalog/runtime authorization은 모두 false다. 이 준비를 live characterization으로 승격하지 않는다.
- 다음 cohesive slice는 외부 승인 record를 `--inspect-only`로 검토하고 사용자가 그 exact candidate digest를 승인한 뒤, current-time preflight를 통과시켜 승인된 Business/Enterprise fixture와 역할로 `NP2C-5a-approved-business-enterprise-live-characterization`을 실행하는 것이다. 사용자 승인 digest·승인된 계정·fixture·evidence 저장 경계가 없으면 중단하며 production UI/API를 구현 오라클이나 데이터 수집 경로로 사용하지 않는다.
- 전체 goal은 active로 유지한다. `.git`은 repository가 아니므로 commit/landed를 주장하지 않는다.

### 2026-08-14 previous NP2C-4e source-replay and adversarial-audit checkpoint

이 historical 항목은 아래 4d 이하 checkpoint보다 최신이지만 current 상태는 위 NP2C-5/v19 설계 checkpoint를 따른다. 4e 구현과 증거는 그대로 보존하고 재구현하지 않는다.

- current workspace runtime은 계속 `2026081317`, name은 `notion_workspace_v2_np2c_lifecycle_subtree_finalize_authority`다. Frozen 1317 checksum은 `sha256:6cb890ccc026435715a1c9171b4d1fd0f74ae6896f63d3cdf66a27b6eec5b61b`, actual schema hash는 `sha256:650a031ff1f7d1794bde06d9f9479794d0c89fa2faba8e07762badfe3c500aad`, object count는 179다. 4e는 schema를 추가하거나 frozen 1316/1317을 수정하지 않았다.
- 독립 감사에서 source Trash commit 뒤 catalog acknowledgement 또는 finalizer fault가 나면 retry가 이미 비활성인 root precondition을 재캡처해 중단되는 실제 recovery 결함을 재현했다. Source adapter의 exact immutable operation replay를 precondition recapture보다 먼저 조회하도록 고쳐, source mutation 재실행 없이 source-applied acknowledgement와 catalog finalization을 수렴시킨다.
- 회귀는 source-receipt acknowledgement loss와 catalog-finalizer response loss, catalog fault phase `after_evidence|after_bindings|after_tree`의 전면 rollback, source/catalog effect bound 11,004 exact parity와 11,005 pre-parse refusal, canonical result/deep-health 및 identifier/path/digest/lease-secret redaction을 고정한다.
- 4d/4e current-tree 증거는 source adapter·bound·finalizer·orchestration focused `32 passed`, adjacent lifecycle/page-tree `41 passed`, 1317 migration/chain/machine lane `9 passed`, machine contract v18 verifier `3 passed`와 CLI `valid=true`다. 첫 read-only parallel hash 명령은 Windows host `0xC0000409`로 실패했고 serial 재실행은 성공했으므로 이를 코드 실패나 새 green으로 숨기지 않는다.
- machine contract v18 status는 `duplicate_trash_restore_1317_source_commit_replay_and_adversarial_audit_contract_tested_default_off`다. Full-owner receipt finalization은 tree를 `complete`로 유지하고 `full_sweep_required=true`는 watermark/cutover debt로 남긴다.
- 이 historical checkpoint가 지정했던 다음 slice `NP2C-5-official-archive-source-contract-design`은 reference-only design으로 완료됐다. 현재 다음 gate는 위 `NP2C-5a` live characterization이며 DDL, runtime, route/UI, resolver, live migration, destructive acknowledgement나 purge를 승인하지 않는다.
- 전체 goal은 active로 유지한다. `.git`은 repository가 아니므로 commit/landed를 주장하지 않는다.

### 2026-08-14 previous NP2C-4d workspace 1317 atomic subtree-finalizer checkpoint

이 historical 항목은 아래 4c 이하 checkpoint보다 최신이지만 current 상태는 위 4e/v18 checkpoint를 따른다.

- current workspace runtime은 `2026081317`, name은 `notion_workspace_v2_np2c_lifecycle_subtree_finalize_authority`다. Frozen 1316 SQL/checksum/actual hash와 exact 1315→1316 migrator는 current runtime과 독립적으로 import·실행 가능하다.
- frozen 1317 checksum은 `sha256:6cb890ccc026435715a1c9171b4d1fd0f74ae6896f63d3cdf66a27b6eec5b61b`, actual schema hash는 `sha256:650a031ff1f7d1794bde06d9f9479794d0c89fa2faba8e07762badfe3c500aad`, object count는 179다. Frozen 1316 checksum/actual은 `sha256:04db7799402ea563228c6be4b016907c0f51ae4ba376fb176bb2a15ca588d81c` / `sha256:cb375ac9e36d42fb3896eabf11f32d6b35a131f5075c1512a51add3da6d32981`다.
- explicit offline/quiesced `notion_workspace_lifecycle_subtree_finalize_migration.py`와 dry-run-default CLI가 exact 1316→1317만 허용한다. Live command/evidence가 있으면 거부하고 source receipt, manifest, effect, mapping 또는 성공을 fabricate하지 않는다. 운영 catalog에는 적용하지 않았다.
- 1317은 실제 source receipt의 transitioned effect와 lifecycle이 변하지 않은 parent/sibling effect를 모두 표현하도록 effect bound를 11,004로 고정하고 `transitioned IN (0,1)` authority와 active→active proof refresh guard를 추가한다. 이 변경은 frozen 1316을 수정하지 않는다.
- `finalize_page_lifecycle_subtree_catalog`는 duplicate/Trash/restore의 binding, locator projection, incarnation attestation, tree node/order, subtree manifest/effects/mappings, workspace/security/tree versions와 operation/audit/outbox를 한 catalog transaction으로 수렴시킨다. Source lineage/receipt와 command lease/fence를 재검증하며 exact replay가 재실행보다 먼저다.
- route-unregistered `notion_workspace_page_lifecycle_subtree_orchestration.py`는 exact rehearsal sentinel, contained catalog/user-data/source paths와 source quiescence 아래 source-v3 adapter와 1317 finalizer만 결합한다. Finalized replay는 sentinel/path/quiescence/source I/O보다 먼저이며 report는 identifier-free counters만 반환한다.
- current-tree evidence는 문서 동기화 후 1317 migration/finalizer/orchestration/page-tree focused `24 passed`, full frozen 1303→1317 migration-chain rehearsal `2 passed`, machine verifier `3 passed`와 CLI `valid=true`다. 그 전 implementation evidence는 1317 migration/kernel/finalizer lane `20 passed`, source adapter/structural/finalizer/rehearsal lane `48 passed`, store/projection/healing/outbox lane `170 passed` 후 test-drift 2건 수정·exact rerun `2 passed`, orchestration core `2 passed`와 import isolation `1 passed`다. Windows host의 한 short rerun은 테스트 출력 `2 passed` 뒤 `0xC0000409`로 끝났고 동일 exact command 재실행은 exit 0이었다. 이 비정상 종료를 새 green으로 숨기지 않는다.
- machine contract v17 status는 `duplicate_trash_restore_1317_atomic_catalog_finalizer_and_offline_rehearsal_contract_tested_default_off`다. 4d는 offline/quiesced `contract-tested`일 뿐 live/differential pass가 아니다. Runtime command, routes/UI, resolver cutover, live migration, continuous worker, destructive watermark ack, official Archive/unarchive, public duplicate와 purge는 모두 false다.
- 이 historical 4d checkpoint가 지정했던 다음 slice는 `NP2C-4e-independent-audit-and-bounded-adversarial-rehearsal`이었고 현재 완료됐다. 그 다음은 `NP2C-5-official-archive-source-contract-design`이며 source version을 임의 선점하거나 runtime에 연결하지 않는다.
- 전체 goal은 active로 유지한다. `.git`은 repository가 아니므로 commit/landed를 주장하지 않는다.

### 2026-08-14 previous NP2C-4c workspace 1316 lifecycle-subtree guard checkpoint

이 historical 항목은 아래 4b 이하 checkpoint보다 최신이지만 current 상태는 위 4d/1317 checkpoint를 따른다. 1316 frozen hashes와 4c evidence는 migration-source 회귀 기준으로 보존한다.

- current workspace runtime은 `2026081316`, name은 `notion_workspace_v2_np2c_lifecycle_subtree_guards`다. Frozen 1315 SQL/checksum/actual hash와 1314→1315 migrator는 current runtime과 독립적으로 import·실행 가능하다.
- frozen 1316 checksum은 `sha256:04db7799402ea563228c6be4b016907c0f51ae4ba376fb176bb2a15ca588d81c`, actual schema hash는 `sha256:cb375ac9e36d42fb3896eabf11f32d6b35a131f5075c1512a51add3da6d32981`, object count는 179다.
- explicit offline/quiesced `notion_workspace_lifecycle_subtree_migration.py`와 dry-run-default CLI가 exact 1315→1316만 허용한다. Live command, historical duplicate/Trash/restore command, ambiguous non-active tree history를 거부하고 source receipt, manifest, effect, mapping, success를 fabricate하지 않는다. 운영 catalog에는 적용하지 않았다.
- 1316은 기존 base action `archive`를 frozen compatibility detail로 보존하면서 immutable canonical action `trash`를 별도로 기록한다. Canonical lifecycle action/state, bounded immutable subtree manifest/effect/object mapping, duplicate pending binding create, exact Trash/restore binding transition, canonical tree transition, configured command/source-receipt/source-generation UDF와 operation/audit/outbox/deep-health correlation을 fail closed로 추가한다.
- machine contract v16 status는 `duplicate_trash_restore_1316_lifecycle_subtree_guards_contract_tested_finalizer_pending`이다. 4a design, 4b source adapter, 4c schema/migration은 true이며 4d catalog finalizer/exact-sentinel rehearsal, runtime, routes/UI, resolver, live migration, watermark ack, official Archive와 purge는 false다.
- 현재 확보한 코드 증거는 4c focused `9 passed`, 1314/1315/1316 adjacent migrations `29 passed`, page-tree/locator/outbox adjacent lane `54 passed`, machine verifier `3 passed`다. 전체 chain과 문서 동기화 후의 최종 재검증 결과는 4c checkpoint에 추가한다.
- 이 historical 4c checkpoint가 지정했던 다음 slice는 `NP2C-4d-atomic-catalog-finalizers-and-exact-sentinel-rehearsal`이었고 현재 완료됐다. 4c 완료 자체는 당시 4d, runtime 등록, live migration 또는 전체 NP-2C 완료를 자동 승인하지 않았다.
- 전체 goal은 active로 유지한다. `.git`은 repository가 아니므로 commit/landed를 주장하지 않는다.

### 2026-08-14 previous NP2C-4b duplicate/Trash/restore source adapter checkpoint

이 항목이 아래 4a, 3e3와 모든 historical checkpoint보다 최신이다. 재개 시 이 항목, machine contract v15, 4b checkpoint와 current hashes를 먼저 확인한다.

- 이 historical 4b checkpoint 당시 workspace runtime은 frozen `2026081315`였고 candidate `2026081316` schema/DDL/migration은 아직 없었다. 현재 상태는 위 4c checkpoint를 따른다.
- 최신 공식 동작은 Archive와 Delete/Recycle Bin을 구분한다. 현재 HTTP DELETE가 `archive_page`를 호출하고 `/trash`에서 복원하는 경로는 canonical `trash/restore`로 정의하며, official `archive/unarchive`는 별도 source-lifecycle gate다.
- `docs/notion_parity/NP2C_DUPLICATE_TRASH_RESTORE_DESIGN.md`는 same-owner duplicate의 domain-separated deterministic page/block/file mapping, exact contained hash-verified byte staging/publish, exact Trash transition cohort, same-incarnation restore, original 또는 reviewed fallback placement를 동결한다.
- Public duplicate는 NP2E에 남고, permanent purge는 retention/legal/backup gate에 남는다. H5e source 1304 예약은 유지하며 official Archive source schema 번호를 선점하지 않는다.
- 이 historical checkpoint 당시 machine contract v15 status는 `duplicate_trash_restore_source_adapters_and_file_recovery_contract_tested_candidate_1316_pending`였다. 당시 schema 1315, candidate 1316, 4a design와 4b source adapter true, 4c schema/4d finalizer-rehearsal/runtime/live migration/permanent delete는 모두 false였다. 현재 상태는 문서 최상단의 v17/1317 checkpoint를 따른다.
- 새 route-unregistered contract/adapter는 frozen v3 ledger 위에서 replay-derived page/block/file/version mapping, exact file staging/no-overwrite recovery, exact Trash cohort와 same-incarnation restore를 구현한다. Focused 18개, adjacent lifecycle/structure 15개, source-v3/incarnation consumer 32개가 temporary shards에서 통과했다.
- verifier CLI는 `valid=true`, adversarial verifier suite는 `3 passed`다. Workspace schema, product route, UI, worker, resolver, live source/catalog은 변경·접근하지 않았다.
- exact hashes와 evidence는 `docs/notion_parity/checkpoints/np2c4b-source-lifecycle-adapters-file-recovery-20260814.json`에 고정한다.
- 정확한 다음 구현 후보는 별도 승인된 `NP2C-4c-frozen-1315-to-1316-lifecycle-subtree-schema-and-guards`다. 4c를 시작하기 전 frozen 1315 independence와 lifecycle/cohort/mapping/file evidence DDL을 다시 감사한다.
- 전체 goal은 active로 유지한다. `.git`은 repository가 아니므로 commit/landed를 주장하지 않는다.

### 2026-08-14 latest NP2C-3e3 workspace 1315 structural-finalize checkpoint

이 항목이 아래 모든 historical checkpoint보다 최신이다. xhigh 재개 시 이 항목, machine contract v13, 새 3e3 checkpoint, current source/hash 순으로 확인한다.

- current workspace runtime/schema는 frozen `2026081315`, name은 `notion_workspace_v2_np2c_structural_finalize_authority`다. Frozen 1314 SQL/checksum/actual hash와 1313→1314 migrator는 current runtime과 독립적으로 import·실행 가능하다.
- frozen 1315 checksum은 `sha256:a57f73b41a834c42edcdfbacbbf2aa1fc30dd34366d98d7c40a42337b4f0e932`, actual schema hash는 `sha256:33eaed5b16671104d53443c7cd4b09b3471eabbce805161608a114e4528f5d82`, object count는 159다.
- explicit offline/quiesced `notion_workspace_structural_finalize_migration.py`와 dry-run-default CLI가 exact 1314→1315만 허용한다. Verified backup과 frozen source/target hashes를 요구하고 live lifecycle command를 거부하며 운영 catalog에는 적용하지 않았다.
- route-unregistered `notion_legacy_page_structural.py`는 frozen source-v3 operation ledger를 재사용한다. create/move/reorder 한 command 안의 여러 page/block DML은 외부에 보이기 전 affected page별 한 logical watermark generation으로 coalesce된다. Immutable source receipt, exact replay-before-renewed-quiescence, rollback과 file identity fence를 유지한다.
- `WorkspaceStore.finalize_page_lifecycle_structural_catalog`는 complete old/new sibling scope와 subtree evidence를 검증하고 binding/projection/attestation/tree, workspace/security/tree versions, structural manifest/effects와 operation/audit/outbox를 한 catalog transaction에 수렴시킨다. Root parent는 null-safe하고 structurally unchanged parent는 unrelated order generation을 유지한다.
- `notion_workspace_page_structural_rehearsal.py`는 finalized catalog replay를 sentinel/path/source I/O보다 먼저 검증한다. 새 effect만 exact rehearsal sentinel, contained paths와 explicit source quiescence를 요구하며 report는 identifier-free다.
- current-tree serial evidence: 3e3 source+sentinel focused `6 passed`; finalizer/migration/structural aggregate `26 passed`; prior lifecycle/tree lane은 최초 42 pass/5 regression 발견 후 rename commitment key fix 뒤 실패 파일 `11 passed`; incarnation orchestration/rebind `37 passed`; projection/locator/incarnation migration `68 passed` 중 historical fixture drift 2건을 1315 chain으로 갱신해 `2 passed`; healing/outbox `86 passed`; complete workspace migration chain `148 passed, 2 skipped`; machine verifier `3 passed`. 문서 동기화 뒤 machine verifier는 다시 `3 passed`와 CLI `valid=true`였지만, structural aggregate와 py_compile 재실행은 Windows `0xc0000409`가 반복되어 host-dependent gate로 중단했다. 이를 새 코드 실패나 fresh aggregate green으로 덮어쓰지 않는다.
- exact source/doc hashes, 명령별 결과와 이후 재발한 host-dependent `0xc0000409` 중단 기록은 `docs/notion_parity/checkpoints/np2c3e3-1315-structural-finalize-rehearsal-20260814.json`에 고정한다.
- 1315 구현은 offline/quiesced `contract-tested`일 뿐 live/differential pass가 아니다. Route/UI/runtime command, live migration, continuous worker, resolver cutover, destructive watermark ack와 purge는 계속 false다.
- 이 historical 3e3 checkpoint가 지정했던 다음 slice는 이후 `NP2C-4a-duplicate-trash-restore-contract-design`으로 완료됐다. 현재 continuation은 문서 최상단의 4a checkpoint와 4b 별도 승인 gate를 따른다.
- 전체 goal은 active로 유지한다. `.git`은 repository가 아니므로 commit/landed를 주장하지 않는다.

### 2026-08-14 previous NP2C-3e2 workspace 1314 structural-guard checkpoint

이 항목이 아래 모든 NP2C historical checkpoint보다 최신이다. 재개 시에는 새 3e2 checkpoint, machine contract v12, current file hashes, 3e design, 3e1 checkpoint 순으로 확인한다.

- current workspace runtime/schema는 frozen `2026081314`, name은 `notion_workspace_v2_np2c_structural_effect_guards`다. Frozen 1313 SQL/checksum/actual hash와 1312→1313 migrator는 current runtime과 독립적으로 import·실행 가능하다.
- frozen 1314 checksum은 `sha256:c131d83534326ab1dfa8bf4d007412e1fb9e08c628c04e36de55287b79835b82`, actual schema hash는 `sha256:2aad855cd6b9775850706f3c23c6ae3a494b908b4bce188791c0533a0e0acf14`, object count는 159다.
- explicit offline/quiesced `notion_workspace_structural_effect_migration.py`와 dry-run-default CLI가 exact 1313→1314만 허용한다. Verified backup, frozen source/target hashes, rollback, invalid tree와 live command refusal을 강제하며 운영 catalog에는 적용하지 않았다.
- 1314는 tree-node `order_generation`, immutable action-specific structural manifest/effect rows, create/move/reorder shape·scope·dense ordinal·binding proof guards, fail-closed command/source-receipt UDF, exact structural result validator와 deep-health correlation을 추가한다. Source receipt나 command를 migration에서 fabricate하지 않는다.
- 구형 `notion_workspace_page_lifecycle.py` v1 orchestration은 1313+ operation namespace와 양립하지 않는다. 현재는 quiescence 확인 뒤에도 catalog/source I/O 전에 명시적으로 거부한다. Rename-v2와 structural-v3 finalizer만 허용한다.
- machine contract는 v12, status `structural_effect_schema_1314_guarded_catalog_finalizers_pending`다. `candidate_1314_schema_implemented=true`이고 create/move/reorder catalog finalizer, sentinel rehearsal, route/UI/runtime/live migration은 false다.
- 현재 확보한 3e2 증거는 structural migrator focused `8 passed`, 1311→1314 migration 묶음 `36 passed`, lifecycle/tree/source-adapter 회귀 `37 passed`, machine verifier `3 passed`다. 최종 adjacent/protected 회귀와 checkpoint hash는 이 continuation의 마지막 단계에서 고정한다.
- 정확한 다음 cohesive slice는 `NP2C-3e3-atomic-catalog-finalizers-and-sentinel-rehearsal`이다. 1314 DDL을 다시 설계하지 말고 create/move/reorder 각각의 source manifest를 catalog effect set과 한 transaction으로 수렴시키며 response-loss, rollback, scope drift, 10,000-sibling generation permutation을 검증한다.
- 3e3가 끝나도 route/UI/runtime command, live migration, continuous worker, resolver cutover, destructive watermark ack, duplicate/archive/restore와 purge는 시작하지 않는다.

### 2026-08-14 previous NP2C-3e1 create/move/reorder source-adapter checkpoint

이 항목은 3e2보다 이전의 source-only checkpoint다. 역사적 입력 계약으로 보존하되 현재 runtime/status 판단에는 위 3e2 항목을 우선한다.

- 이 historical checkpoint 시점의 workspace runtime/schema는 frozen `2026081313`이었고 candidate `2026081314`는 설계 번호였다. 현재 상태는 위 3e2 checkpoint를 따른다.
- legacy source는 새 migration 없이 frozen v3 immutable operation ledger를 create/move/reorder 전용 action/result contract로 재사용한다. H5e source v4는 conditional ack용으로 예약돼 있으며 NP2C가 사용하지 않는다.
- create는 replay-derived proposed page ID, exact root/parent shard authority, parent edit/lock와 bounded blocks/version을 요구한다. Move/reorder는 resulting parent로 action을 구분하고 old/new sibling scope, subtree, parent block 및 source-mutated page post-manifest를 exact commit한다.
- hard bounds는 subtree 1,000/depth 256, old/new sibling scope 각 10,000, union receipt 21,002, aggregate source materialization 256 MiB, parent blocks 5,000, source result JSON 16 MiB다. 모든 bound는 source write 전에 검사한다.
- 1313은 nonexistent create target, multi-page effect evidence와 full 10,000-node ordinal permutation을 표현할 수 없다. 1314 design은 immutable command scope/effect evidence, generation-scoped sibling order, action guards, lifecycle-only create/multi-proof authority와 exact deep health를 요구한다.
- route-unregistered source contract/adapter는 구현됐다. Deterministic create, cross-parent move, same-parent reorder와 structure-equal compatibility write, exact response-loss replay, rollback/stale/cycle/corrupt-ledger, secret-free result 및 import isolation을 temporary v3 shard에서 검증했다. Focused 6 pass, adjacent frozen source regressions 24 pass다.
- machine contract는 v11, status `create_move_reorder_source_adapters_contract_tested_catalog_1314_pending`다. `create_move_reorder_source_adapters_implemented=true`이지만 `candidate_1314_schema_implemented`, catalog finalizers, rehearsal, routes, runtime, live migration은 모두 false다.
- 다음 구현은 자동 승인되지 않는다. 승인 시 `NP2C-3e2 frozen 1313→1314` → `3e3 finalizers/rehearsal` 순서로 각각 checkpoint한다.

### 2026-08-14 latest NP2C-3d exact-sentinel rename rehearsal checkpoint

이 항목이 아래 NP2C-3c 및 날짜별 historical checkpoint보다 최신이다. 재개 시에는 이 항목, 새 3d checkpoint, current source/hash 재감사 순으로 우선한다.

- workspace runtime/schema는 계속 frozen `2026081313`이다. NP2C-3d는 schema, migration, route, worker, resolver, UI 또는 feature flag를 추가하지 않았다.
- 새 route-unregistered `notion_workspace_page_lifecycle_rehearsal.py`가 frozen legacy-v3 rename source adapter와 1313 atomic catalog finalizer만 exact rehearsal sentinel·source quiescence 아래 결합한다.
- `WorkspaceStore.get_page_lifecycle_rename_finalized_replay`는 current admin과 immutable command/operation/audit/outbox commitment를 검증한 full v2 result를 source/filesystem I/O 없이 반환한다. Orchestration은 이 replay를 첫 external read로 사용하고, requested title과 original prestate로 exact source request/receipt를 다시 계산하므로 changed-title operation-ID reuse도 path access 전에 충돌한다.
- changed path는 source revision/watermark/source epoch/full-sweep, catalog binding/workspace/security를 각각 정확히 한 번 전진시키고 target projection/node를 갱신한 뒤 tree를 `partial`로 남긴다. No-op은 source/catalog domain version을 보존하고 양쪽 immutable receipt만 남긴다.
- source commit 뒤 response loss, catalog commit 뒤 response loss, source commit 뒤 catalog outbox rollback은 각각 durable replay로 수렴한다. 이는 cross-database exactly-once가 아니라 at-least-once/state-idempotent saga다.
- aggregate report에는 raw title, owner/page identifier, source/catalog path, digest, token, filename 또는 lease secret이 없다. Import는 app/auth/config/routes/Second Brain/Knowledge Core를 불러오거나 filesystem side effect를 만들지 않는다.
- current-tree serial evidence는 3d focused `5 passed`, source adapter/kernel/finalizer `19 passed`, v3 migration/page-tree/migration chain `43 passed`, locator/projection/healing/outbox `90 passed`, protected Notion route/auth `74 passed`, Second Brain chat characterization `2 passed`다. Machine contract는 v9, status `rename_end_to_end_rehearsal_contract_tested_default_off`다.
- exact hashes와 명령은 `docs/notion_parity/checkpoints/np2c3d-sentinel-bound-rename-rehearsal-20260814.json`에 고정한다. `.git`은 여전히 repository가 아니므로 commit/landed를 주장하지 않는다.
- 정확한 다음 slice는 `NP2C-3e-create-move-reorder-contracts`다. 먼저 세 action의 identity, source pre/post receipt, sibling/tree CAS, replay와 rollback을 design/machine-contract로 분리 동결한다. Rename 구현을 일반화해 복사하지 않으며 새 schema 필요 여부는 설계·감사 전에는 결정하지 않는다.
- NP2C-3e에서도 route/UI/runtime, continuous worker, resolver cutover, H5e source v4/ack, duplicate/archive/restore와 purge를 시작하지 않는다. 전체 goal은 active로 유지한다.

### 2026-08-14 previous NP2C-3c / workspace 1313 handoff checkpoint

이 항목이 아래의 날짜별 historical checkpoint보다 최신이며, 서로 충돌할 때 이 항목과 current source/hash 재감사가 우선한다.

- workspace runtime은 `2026081313`, name은 `notion_workspace_v2_np2c_rename_catalog_finalize`다. Frozen 1312는 독립 상수와 migrator target으로 보존된다.
- frozen 1313 checksum은 `sha256:6be5b47275cf0dbe0b6b244c3f71672662f8ce648d1efa446208a94488da1637`, actual schema hash는 `sha256:e971256344e262c59990901b3a6816ecac7be760a31c286f68c5e03d4d491595`, object count는 144다.
- NP2C-3c는 exact 1312→1313 migrator/CLI, lifecycle-only fail-closed DDL authority, changed/no-op atomic catalog finalizer와 focused tests를 구현했다. Changed finalize는 binding proof·generation, incarnation attestation, complete locator projection, target tree node와 workspace/security version을 한 transaction에 갱신하고 tree state를 `partial`로 내린다. No-op은 domain state version을 올리지 않는다.
- legacy v1 fake finalizer와 unconfigured raw SQL proof refresh는 DB level에서 거부된다. Callback 중 lease expiry, workspace CAS drift와 outbox fault는 write 0 또는 full rollback이다. Result/audit/outbox에는 raw title이 없다.
- current machine contract는 v8, status `rename_catalog_finalizer_contract_tested_default_off`, current/candidate schema `2026081313`이며 runtime commands, permanent delete, resolver, continuous worker, destructive watermark ack와 live migration은 모두 false다.
- 이 checkpoint의 source와 catalog는 임시 fixture뿐이다. 운영 catalog/shard migration, real user data access, route/UI/runtime 등록, resolver cutover를 승인하지 않는다.
- exact hashes, serial test commands, host-dependent abort 기록과 다음 명령은 `docs/notion_parity/checkpoints/np2c3c-1313-atomic-catalog-rename-finalize-20260814.json`에 고정한다.
- 정확한 다음 slice는 `NP2C-3d-sentinel-bound-rename-end-to-end-rehearsal`이다. 새 schema나 route를 만들지 않고 이미 고정된 v3 source adapter와 1313 catalog finalizer를 exact rehearsal sentinel 아래 결합해 양쪽 response-loss/rollback/no-op/redaction/partial-tree/full-sweep-debt를 증명한다.
- 3d가 끝나기 전 create/move/reorder, duplicate/archive/restore, H5e source v4/ack, resolver/UI/runtime을 시작하지 않는다.
- 현재 tool goal은 active일 수 있지만 이 문서가 goal을 생성·교체·완료할 권한은 주지 않는다. xhigh는 매 재개 때 `get_goal` 결과와 이 문서의 상태를 checkpoint에 함께 기록하고, 전체 parity가 끝나기 전 goal을 complete로 바꾸지 않는다.

### 2026-08-14 resumed NP2C-0/1 page-tree foundation checkpoint

- NP2B-3 default-off UI 뒤 다음 slice로 NP2C page tree/lifecycle을 선택했다. legacy source는 content와 physical tree authority로 유지하고 catalog tree는 generation-fenced projection으로만 둔다. cross-database exactly-once를 주장하지 않는다.
- `NP2C_PAGE_TREE_LIFECYCLE_DESIGN.md`와 machine contract v2가 full owner+page parent identity, source incarnation/fence/change receipt, bounded subtree/depth/sibling scope, prepared→source_applied→finalized/failed/manual_audit saga와 no-go gate를 고정한다.
- workspace runtime은 `2026081312`다. frozen 1311 위에 최대 300초 lifecycle lease, exact claim/reclaim/heartbeat fencing, live source-applied/finalize 전이와 command↔workspace-operation namespace guard를 추가했다. 1311의 tree state/node/saga foundation은 독립 frozen 계약으로 유지하며 migration은 pre-kernel command row를 한 건이라도 발견하면 fail-closed 거부한다.
- explicit dry-run 기본 1310→1311 migrator/CLI를 추가했다. request/startup migration, 운영 catalog apply와 live data access는 없다.
- frozen 1311 checksum은 `sha256:bb88d9c7e7937d0e29086d20ebd0c7a567658678daca0dd31c931634431cf723`, actual schema hash는 `sha256:7ef7fcee1e78a641388de16e55937ad73fd549d97aa33de7221c215d19da9517`, schema object count는 140이다. frozen 1310 checksum/actual은 `sha256:72058812be6f0b92db756d5648d2cd9a0741d4ad4f4f9c62377d128ca0b581b3` / `sha256:d8935af134fa12cdd5f0f803e15cba798dece1831cfeeee7673e0dc0ef7d7b33`로 독립 보존한다.
- 현재 구현은 NP2C-2a offline initial materializer까지다. `notion_workspace_page_tree.py`는 replay-before-path, exact sentinel/quiescence, active attested binding 최대 1,000개, owner별 source snapshot 2회/full integrity 1회, hidden-parent/cycle/source-drift write-0, parent-first dense ordering을 강제한다. store는 tree state/nodes/operation/audit/outbox를 한 transaction에 commit하고 exact replay commitment를 검증한다. 운영 등록·source mutation·watermark ack·live migration은 없다.
- 1312 lifecycle trigger-hardening migration과 그 위의 NP2C-2b disabled command kernel은 구현됐다. Store는 prepare/claim/reclaim/write-free same-token acknowledgement/record-source/finalize/fail/manual-audit, exact command/finalized commitment replay와 deep-health를 제공한다. Disabled orchestration은 quiescence-first new effect, replay-before-quiescence terminal/finalized response, fake temp-source immutable effect ledger, source response-loss 및 catalog finalize outbox fault 수렴을 검증한다. 실제 legacy rename/title/source pre-state, tree·binding·locator projection refresh는 구현하지 않았다. legacy source adapter, protected routes/default-off UI, runtime commands, resolver cutover, continuous worker, destructive watermark ack와 permanent purge는 계속 false다.

### 2026-08-14 resumed NP2C-2b disabled lifecycle kernel checkpoint

- `notion_workspace_store.py`에 route-unregistered lifecycle APIs와 exact finalized commitment validator를 추가했다. Finalize replay는 source callback 전에 해결되고, 새 finalize는 source receipt를 catalog write 전과 write transaction 안에서 모두 검증한다.
- `notion_workspace_page_lifecycle.py`는 injected replay-safe source effect만 받는 disabled harness다. `source_quiesced_confirmed=false`인 새 command는 catalog/source write 0이고, 이미 finalized/failed/manual-audit인 exact replay는 quiescence와 source callback보다 먼저 반환한다.
- focused evidence는 kernel `7 passed`, disabled orchestration `6 passed`, 1312 migration `12 passed`, page-tree materializer `7 passed`, workspace store `18 passed`다. 결합 pytest가 두 차례 Windows `0xC0000409`로 collection 출력 전에 종료돼 broad 결합 명령은 중단하고 파일별 serial evidence만 채택했다.
- NP2C-3a 설계 gate와 NP2C-3b temporary-shard source adapter는 아래 checkpoint에서 완료됐다. 다음 정확한 slice는 `NP2C-3c-1313-atomic-catalog-rename-finalize`다. 별도 frozen 1312→1313 lifecycle authority와 atomic catalog finalizer만 구현하며 routes/UI/runtime, resolver, ack/purge/live migration은 시작하지 않는다.

### 2026-08-14 resumed NP2C-3a rename source-adapter design checkpoint

- machine contract v6와 `NP2C_PAGE_TREE_LIFECYCLE_DESIGN.md` 14.3이 source request/receipt와 catalog finalize 경계를 동결한다. 이 slice는 문서·read-only machine contract뿐이며 real source/catalog mutation은 0이다.
- `page_save_operations`는 full page/block result, page-delete cascade, immutable UPDATE/DELETE guard 부재, frozen source object 인증 부재와 optional migration 의존 때문에 lifecycle replay authority로 사용하지 않는다.
- 새 source migration은 필요하지 않다. Frozen source v3 `2026081303`의 immutable `notion_legacy_change_consumer_operations`를 fixed consumer `np2c.lifecycle`, action `legacy.page_lifecycle.rename`, result contract `np2c_legacy_page_rename_receipt_v1`로 재사용한다. H5e conditional-ack source v4 `2026081304` 예약은 그대로다.
- changed rename은 exact source identity/incarnation을 보존하며 revision, target watermark generation, source epoch, full-sweep generation을 각각 1 증가시킨다. Exact normalized-title no-op은 source state를 바꾸지 않고 immutable `changed=false` receipt만 남긴다. Raw title은 catalog command/result/audit/outbox/checkpoint/report/log에 저장하지 않는다.
- 현재 proof는 linked-page title/icon/archive를 hydrate하므로 target rename이 다른 page proof를 바꿀 수 있다. Changed rename catalog finalize는 target을 원자 갱신하되 tree state를 `partial`로 내려 exact owner full sweep debt를 보존해야 하며 partial 상태에서 resolver cutover는 금지다.
- Frozen 1312의 active binding refresh는 locator-healing authority만 허용하므로 lifecycle command가 이를 사칭해서는 안 된다. 별도 1312→1313 lifecycle-specific guard/finalizer가 필요하다고 결정했지만 아직 DDL/migration/API는 없다.
- 이 설계의 다음 구현은 아래 NP2C-3b checkpoint에서 완료됐다. Catalog 1313, routes/UI/runtime, resolver, ack/purge/live migration은 이 설계 slice에서 건드리지 않았다.

### 2026-08-14 resumed NP2C-3b v3-ledger rename source-adapter checkpoint

- 새 `notion_legacy_page_lifecycle_contract.py`와 `notion_legacy_page_lifecycle.py`만 추가해 frozen source v3 operation table을 `np2c.lifecycle`/`legacy.page_lifecycle.rename` namespace로 재사용했다. 기존 `notion_store.py`, source DDL/migration, workspace store/schema, protected routes는 수정하지 않았다.
- adapter는 caller-owned exact file-backed connection에서 full v1→v2→v3 integrity를 한 번 인증한다. New effect는 quiescence를 요구하지만 exact operation replay는 그 전에 operation ledger만 읽고 반환한다. 요청 hash는 normalized title, actor, catalog command/caller operation, complete expected pre-state를 commit하며 raw title은 persisted result나 evidence에 없다.
- changed rename은 하나의 page UPDATE로 title/timestamp/revision을 함께 바꿔 frozen watermark trigger를 정확히 한 번 실행한다. revision, target change generation, source epoch, full-sweep generation은 각각 `+1`, incarnation은 동일하다. No-op은 source page/generation/version을 바꾸지 않고 `changed=false` immutable receipt만 쓴다.
- writer는 source identity, active owner/page/incarnation, revision capability, edit role, lock, exact watermark/pre-state를 `BEGIN IMMEDIATE` 안에서 재검증한다. Permission ancestry 256 hop, version snapshot 10,000 blocks/16 MiB UTF-8 bound가 있으며 snapshot/operation/commit fault는 전체 rollback이다.
- focused current-tree evidence는 source adapter `6 passed`, adjacent v3 consumer `9 passed`, v3 migration `8 passed`, v2 incarnation migration `10 passed`, disabled lifecycle kernel/machine contract lane `16 passed`다. Real user shard/live migration/runtime registration은 0이다.
- 다음 정확한 slice는 `NP2C-3c-1313-atomic-catalog-rename-finalize`다. 먼저 frozen 1312→1313 lifecycle-only guard와 raw SQL/deep-health/fault contract를 분리 구현하고, sentinel-bound source+catalog rehearsal은 NP2C-3d까지 금지한다.

### 2026-08-13 resumed H1/H2 checkpoint

- active goal을 재확인하고 `AGENTS.md`와 이 문서를 다시 읽은 뒤 current-tree baseline을 실행했다.
- proof actor와 immutable `bound_by` provenance를 DTO, projection capture/revalidation, incarnation orchestration에서 분리했다. rebound generation의 source actor는 exact immutable rebound commitment에서만 owner로 도출하며 malformed commitment는 fail-closed한다.
- readiness, proof-actor 판정, public replay helper, direct attest/rebind mutation replay가 하나의 canonical operation/authorization/result/attestation/audit/outbox/projection commitment validator를 사용한다.
- malformed result ledger 회귀에서 readiness=false, direct retry/public replay 실패, source callback 0회를 검증했다.
- H1 evidence: incarnation/offline lane `54 passed`, projection/healing/reader adjacent lane `69 passed`, workspace migration chain `100 passed, 2 skipped`, route/auth `36 passed`, parity/evidence verifier lane `63 passed`. H3/H4와 shared deep-health validator 수렴 뒤 current incarnation/offline/restore lane은 `59 passed`, store/outbox/healing/locator lane은 `135 passed`로 다시 통과했다.
- `incarnation_attestation_readiness()`는 runtime route가 아닌 explicit admin maintenance scan이다. 현재 exactness를 위해 active generation 전체를 검사하므로 runtime resolver의 request path에 호출하지 않는다. resolver cutover 전에는 keyset/precomputed readiness gate를 별도 설계해야 한다.
- schema DDL은 변경하지 않았다. workspace 1309 checksum은 `sha256:ecc2a8b40e8d114015e05b1772701dd97bb99ac513db9fd041626408f5f127ec`, actual schema hash는 `sha256:d79ecc5b79faacf3ca4db288e7978b8192df91b8c3f9e1d5a99f0d859e2e06b1`로 유지된다.
- live catalog/shard migration, destructive watermark ack, continuous consumer, resolver/UI registration은 실행하지 않았다.

### 2026-08-13 resumed H5a checkpoint

- H5의 첫 non-destructive 영속 경계를 기존 schema `2026081309` 위에 구현했다. 새 DDL이나 migration은 추가하지 않았다.
- `workspace_healing_runs`의 admin-scoped lease/CAS와 `workspace_healing_source_fences`를 재사용하고, page effect operation ID를 run fence/version뿐 아니라 source epoch, full-sweep generation, catalog binding generation에 결속했다.
- `workspace.page_source_healed`의 immutable result/audit/outbox payload에는 identifier-free source receipt(source epoch, full-sweep generation, page change generation/mask, physical-exists/active state)가 저장된다. direct replay와 public effect replay는 같은 exact receipt validator를 사용하며 malformed ledger는 fail-closed한다.
- effect commit 뒤 checkpoint 전에 실패한 재현에서 재시도는 catalog에 고정된 owner fence와 현재/직전 binding generation으로 operation ledger를 먼저 조회해 source resolver와 source DB를 열지 않고 effect를 재사용한 뒤 checkpoint를 한 번만 반영한다.
- H5a evidence: healing apply `21 passed`, adjacent store/outbox/healing/locator `117 passed`. schema checksum/actual hash는 1309 frozen 값 그대로다.
- source watermark 자체는 owner-global durable inbox로 유지된다. continuous source scan, consumer lag loop, source-side generation-conditional DELETE/ack는 아직 구현하거나 활성화하지 않았고, 운영 DB/shard에는 접근하지 않았다.

### 2026-08-13 resumed H5b source-local coordination checkpoint

- legacy owner shard의 frozen additive version `2026081303` (`notion_legacy_change_consumer_v3`)을 추가했다. workspace catalog의 동일 숫자 lineage와는 별개의 source-shard migration namespace다.
- `notion_legacy_change_consumer_passes`, immutable page receipt work row, immutable operation ledger를 source owner DB 안에 둔다. 한 consumer는 running pass 하나만 가지며 trusted clock, lease-token digest, fencing generation, row-version CAS로 start/materialize/reclaim을 보호한다.
- materialization은 고정 source identity·source epoch·full-sweep generation 아래 page-id keyset을 최대 32개만 읽는다. latest incarnation lookup은 selected page마다 `(page_id,incarnation_generation)` primary key를 사용해 매 batch의 history 전체 집계를 제거했다.
- page work receipt는 incarnation nonce/generation, physical-exists/active/retired, watermark generation/mask와 full source fence의 canonical digest를 보존한다. pass report는 opaque pass ID와 identifier-free counters만 반환한다.
- source watermark는 읽기 전후 byte-for-byte 유지되며 DELETE/clear/ack API가 없다. `full_sweep_required`는 durable obligation 신호일 뿐 catalog-wide full-sweep 완료 증거가 아니며, effect work claim/terminal transition과 conditional ack는 다음 slice로 남긴다.
- exact v2 prerequisite, deterministic dry-run checksum, verified single-link backup, copy-on-write image, handle-relative atomic replace와 already-current 수렴을 사용하는 명시적 v2→v3 migrator/CLI를 추가했다. request/startup import와 운영 적용은 없다.
- 기존 v1/v2 reader와 incarnation orchestration은 v1-only source에는 기존 검사를 유지하고, v2/v3 source에는 가장 최신 frozen trigger/marker/object chain 전체를 인증한다. v3 설치가 정상 successor인데도 rogue-trigger로 오인하던 호환성 gap을 닫았다.
- current-tree evidence: H5b/H5a·legacy chain·reader·orchestration 통합 lane `89 passed`, projection·incarnation attestation/rebind·offline rehearsal·1309 migration 인접 lane `70 passed`. workspace schema/checksum은 1309 frozen 값에서 변경되지 않았다.
- H5b checkpoint 시점에는 continuous shadow consumer가 아니었고 exact-path runtime writer/orchestrator, pending-work effect terminal, catalog-wide full-sweep completion, generation-conditional source ack, resolver/UI/flag 연결이 비활성·미구현이었다. 바로 아래 H5c checkpoint가 그중 offline terminal/completion 경계만 후속 구현한 최신 상태다.

### 2026-08-13 resumed H5c offline convergence checkpoint

- source v3 schema와 workspace 1309 DDL/checksum은 바꾸지 않았다. materialized pass의 pending work를 page ID 오름차순으로 최대 32개 읽고, 기존 locator-apply run의 page effect가 catalog operation/audit/outbox에 먼저 commit된 뒤 durable `applied` result만 source terminal receipt로 CAS 전환한다. H5e 감사에서 `superseded` effect는 새 binding에 재적용할 pending debt로 바로잡았다.
- catalog effect commit 뒤 source terminal 전 crash에서는 page-effect ledger replay가 source resolver보다 먼저 실행된다. source terminal commit 뒤 catalog checkpoint 전 crash에서는 catalog effect와 source terminal이 모두 exact replay되고 checkpoint counter/cursor만 한 번 수렴한다. source DB transaction을 catalog I/O 동안 유지하지 않는다.
- 명시한 workspace에 없는 source work는 `no_binding`으로 추론하지 않고 pending으로 남긴다. 이는 catalog-global existence oracle과 잘못 선택한 workspace의 false terminal을 막는 보수적 경계다.
- required full sweep은 pending work가 0이고, current workspace admin이 exact owner fence에 결속된 `locator_apply` run의 `completed/has_more=false` 상태를 증명한 경우에만 source immutable operation ledger에 완료 증거를 남긴다. exact replay는 quiescence/catalog I/O보다 먼저이며, 완료 증거가 없는 required sweep 뒤에는 다음 pass를 시작할 수 없다.
- full-sweep 완료는 traversal completion 증거일 뿐 resolver readiness 또는 source watermark acknowledgement가 아니다. watermark row는 byte-for-byte 유지되고 DELETE/clear/ack API, continuous worker, runtime registration, resolver/UI/flag 연결은 계속 없다.
- current-tree evidence: source v1→v2→v3·reader·consumer·H5c orchestration lane `64 passed`, healing/projection adjacent lane `78 passed`, incarnation/rebind/rehearsal lane `47 passed`, Notion manifest/catalog/route/evidence/differential machine-contract lane `79 passed`; H5c focused current-tree lane은 `11 passed`다. 한 대형 병합 명령은 테스트 출력 전 Windows `0xC0000409`로 종료됐고 분리 실행은 모두 통과했다.

### 2026-08-13 resumed H5d multi-workspace no-ack rehearsal checkpoint

- 한 owner shard의 page가 여러 workspace에 분산될 수 있으므로 마지막 workspace run 하나를 owner-wide completion으로 과대 해석하지 않는다. 새 set completion은 최대 32개의 explicit workspace run을 workspace/run 순서로 canonicalize하고, terminal source work가 참조한 distinct workspace set과 exact 일치해야 한다. duplicate workspace, 빠진 workspace, `ignored/no_binding` work가 있으면 fail-closed한다.
- 각 run은 current workspace admin, `locator_apply`, `completed`, `has_more=false`, exact owner source fence를 독립적으로 검증한다. set result/request hash는 모든 run counter와 fence를 immutable source operation에 결속하며, single-run completion은 실제 terminal workspace가 0~1개일 때만 허용한다.
- identifier-free lag report는 pending work가 있거나 required sweep completion이 없으면 `lagging=true`다. 두 workspace rehearsal에서 첫 run 뒤 pending 1, 두 번째 run 뒤 pending 0/completion 미기록, exact set completion 뒤 lag 0을 검증했다.
- source/catalog store를 다시 연 뒤 exact set replay는 catalog callback 전에 반환된다. watermark를 ack하지 않고 시작한 다음 pass는 같은 두 receipt를 `duplicate=2`로 분류하고 새 work를 만들지 않으며 watermark 2개는 그대로 유지된다.
- H5d evidence: source v1→v2→v3·reader·consumer·multi-workspace orchestration lane `65 passed`; consumer/H5d focused lane `12 passed`. H5c의 workspace adjacent `78 passed`, incarnation adjacent `47 passed`, machine contract `79 passed` 증거는 그 checkpoint에 보존된다.
- 아직 source DELETE/clear/conditional ack, continuous scheduler/worker, runtime route/import, resolver/UI/flag 연결은 없다. 다음 destructive 단계는 별도 승인과 새 fault/restore 계약 없이는 시작하지 않는다.

### 2026-08-13 resumed H5e design-only checkpoint

- H5e 감사에서 catalog `superseded` generation을 source-terminal로 만드는 것이 안전하지 않음을 확인했다. coalesced watermark는 새 binding generation에 다시 적용해야 할 수 있으므로 현재 orchestration은 effect가 `superseded`이면 source work를 pending으로 보존한다.
- completion과 lag는 `ignored` 또는 `superseded` work를 acknowledgement 불가로 취급한다. successor contract inspector는 latest incarnation에 대한 watermark coverage와 materialized work보다 current watermark generation/mask가 뒤로 가지 않는지도 검사한다.
- current-tree focused consumer lane은 `9 passed`, orchestration lane은 `5 passed`, legacy source v1→v2→v3·reader·consumer·orchestration lane은 `67 passed`로 재검증했다.
- `docs/notion_parity/H5E_CONDITIONAL_ACK_DESIGN.md`와 `h5e_conditional_ack_contract.json`은 source v4 후보, single logical ack consumer, exact eligibility, transaction/crash/restore/redaction/rollout gate를 정의한다. machine contract는 `status=proposed_not_authorized`, `implemented=false`, `destructive_ack_enabled=false`를 고정한다.
- 이 checkpoint는 destructive ack 구현 또는 실행 승인이 아니다. source v4 migration/API, live shard apply, continuous scheduler, resolver/UI/flag는 계속 금지 상태다.

### 2026-08-13 resumed NP2B-0 navigation design checkpoint

- H5e destructive 영역을 건드리지 않고 병렬 가능한 다음 vertical slice로 server-backed navigation state를 선택했다. NP-2C는 cross-DB lifecycle mutation, H6 resolver cutover는 H5e readiness에 직접 의존하지만 sidebar preference와 favorites는 독립적으로 backend contract를 만들 수 있다.
- current official reference는 sidebar/Home에 Recents, Favorites, Teamspaces, Shared, Private와 section show/order/hide, sidebar resize/collapse가 있음을 뒷받침한다. persistence/conflict/retention은 live characterization 전까지 unknown으로 남긴다.
- `NP2B_NAVIGATION_STATE_DESIGN.md`와 machine contract는 candidate workspace `2026081310`, current-member authorization, generation-stable favorite identity, current-visibility filtering, independent preference/favorite CAS, atomic operation/audit/outbox와 zero-source-I/O snapshot을 고정한다.
- high-frequency Recents는 immutable audit/outbox retention과 coalescing 결정 전까지 첫 slice에서 제외한다. backend schema, routes, UI는 모두 `implemented=false`/disabled다.
- 구현 전 current baseline으로 `test_notion_workspace_store.py`, `test_notion_workspace_pages.py`, `test_notion_workspace_routes.py`, `test_migrate_notion_workspace_incarnation.py`를 함께 실행해 `40 passed`를 확인했고, NP2B machine-contract verifier test는 `3 passed`다. 이는 NP2B-1 구현 완료 증거가 아니라 pre-edit 기준선이다.

### 2026-08-14 resumed NP2B-1 backend foundation checkpoint

- workspace runtime schema는 `2026081310`, name은 `notion_workspace_v2_np2b_navigation_state`다.
  frozen 1309는 독립 상수로 보존된다.
- 새 additive state는 `workspace_navigation_states`, `workspace_sidebar_section_preferences`,
  `workspace_page_favorites`와 raw/service-context triggers/index로 구성된다. read-time defaults는 canonical
  row를 만들지 않는다.
- `get_navigation_state`, `replace_sidebar_preferences`, `add_page_favorite`,
  `remove_page_favorite`는 route-unregistered store API다. current membership, current catalog visibility,
  independent preference/favorite CAS, exact replay, atomic operation/audit/outbox를 적용한다.
- explicit dry-run 기본 1309→1310 migrator/CLI를 추가했다. request/startup migration과 운영 apply는 없다.
- frozen 1310 checksum은 `sha256:72058812be6f0b92db756d5648d2cd9a0741d4ad4f4f9c62377d128ca0b581b3`,
  actual schema hash는 `sha256:d8935af134fa12cdd5f0f803e15cba798dece1831cfeeee7673e0dc0ef7d7b33`이다.
- 격리 evidence: navigation 14 passed, store 결합 32 passed, pages/routes 14 passed,
  locator/outbox 37 passed, migration 구성요소 96 passed/1 platform skip. 최종 chain rehearsal과
  전체 checkpoint 검증은 이 continuation의 H4/H6에서 다시 고정한다.
- machine contract v4 상태는 `ui_contract_tested_default_off`다.
  backend schema/store API, protected routes와 UI implementation은 true이고 runtime UI flag,
  Recents/resolver/live migration은 false다.
- NP2B-2 independent review는 navigation mutation JSON을 `application/json`, 64KiB streaming read,
  duplicate-key/non-finite rejection으로 harden했다. P0는 발견되지 않았고 확인된 P1 두 건은 focused
  negative regression으로 닫혔다.
- 최종 audit checkpoint는
  `docs/notion_parity/checkpoints/np2b2-workspace-navigation-routes-audit-20260814.json`이다.
- NP2B-3 default-off UI와 P0/P1 audit checkpoint는
  `docs/notion_parity/checkpoints/np2b3-workspace-navigation-default-off-ui-20260814.json`이다.
- 최종 checkpoint는
  `docs/notion_parity/checkpoints/np2b1-workspace-navigation-backend-20260814.json`이다.

현재 인계 문서는 H1~H5e와 NP2B-1의 누적 상태를 설명한다. Shared Knowledge 문서,
`AGENTS.md`, live data는 수정하지 않는다. code/test/doc의 정확한 변경 범위와 해시는 각 slice
checkpoint를 source of truth로 사용한다.

## 0. 이 문서의 목적

이 문서는 다음 네 문제를 동시에 해결하기 위한 인계 문서다.

1. 현재 코드가 기존 계획 문서보다 앞서 온 1308→1309→1313 drift와 그 frozen lineage를 정확히 기록한다.
2. 다른 Shared Knowledge 개발과 파일·goal·테스트·상태 문서 충돌 없이 Notion parity를 이어간다.
3. xhigh가 현재 미완성 코드를 다시 설계하거나 성급하게 runtime에 연결하지 않고, 남은 P0/P1부터 순서대로 닫게 한다.
4. NP-2A 이후 NP-0~NP-11 전체 계획과 각 phase의 완료 gate를 한 곳에서 파악하게 한다.

이 문서는 기존 마스터 플랜과 로드맵을 대체하지 않는다. 현재 source와 문서 사이의 차이를 인계하고, 그 차이를 안전하게 해소하는 실행 순서를 정한다. 기존 계획 문서는 H2 단계에서 소유권과 기준선이 확보된 뒤 동기화한다.

## 1. 가장 먼저 지켜야 할 충돌 방지 규칙

### 1.1 AGENTS.md

- code_assist_v1 아래의 모든 변경에는 code_assist_v1/AGENTS.md가 적용된다.
- AGENTS.md는 읽기 전용이다. 이 Notion 작업에서 수정하지 않는다.
- Shared Knowledge 목표를 수행할 때는 AGENTS.md가 지정한 docs/knowledge_spaces 문서를 순서대로 완독해야 한다.
- 이 문서는 Notion parity 전용이다. Shared Knowledge 문서·schema·status를 Notion 작업의 부수 효과로 변경하지 않는다.
- Notion 작업이 Shared Knowledge 결정이나 completion gate를 건드려야 한다면 즉시 중단하고 해당 작업 소유자에게 이관한다.

### 1.2 기본 보호 범위

명시적인 파일 소유권 이전과 별도 승인 전에는 다음 경로를 수정하지 않는다.

- code_assist_v1/AGENTS.md
- code_assist_v1/docs/knowledge_spaces/**
- code_assist_v1/knowledge_core/**
- code_assist_v1/tests/knowledge_spaces/**
- code_assist_v1/app_code.py
- code_assist_v1/routes_notion_bridge.py
- code_assist_v1/routes_chat_stream.py
- code_assist_v1/auth.py
- code_assist_v1/config.py
- code_assist_v1/data_paths.py
- code_assist_v1/tests/conftest.py
- code_assist_v1/test_safety.py 및 test-path safety 장치
- 실제 knowledge/, user_data/, .runtime/, sessions/, workspace/

Second Brain의 source, publication, session, thought, Knowledge Core 데이터와 schema는 Notion migration의 일부가 아니다.

### 1.3 동시 개발과 파일 소유권

- shared tree의 파일은 다른 개발자가 동시에 수정할 수 있다.
- 구현 시작 전에 수정 허용 파일 allowlist를 선언하고 각 파일의 size, mtime UTC, SHA-256을 기록한다.
- 수정 직전에 다시 hash를 계산한다.
- hash 또는 mtime이 달라졌으면 기존 계획을 폐기하고 read-only 재감사를 수행한다.
- 소유권을 받지 않은 shared 파일은 수정하지 않는다.
- 새 파일을 추가하는 방식이 동일 목적을 충족하면 shared 파일 수정보다 우선한다.
- 다른 사람의 변경을 정리, 재포맷, 이동, revert하지 않는다.

### 1.4 Git 상태

기준 시각의 G:\AI_Agent\DEMOS_CODEX\.git은 정상 Git metadata가 아닌 빈 디렉터리로 관찰됐다.

- git status, rev-parse, diff 결과를 신뢰할 수 없다.
- git init을 실행하지 않는다.
- 현재 tree의 코드를 “커밋됨”, “병합됨”, “landed”라고 표현하지 않는다.
- 파일 inventory는 hash와 mtime 기반 snapshot이라고 표시한다.
- Git metadata가 외부에서 복구되기 전에는 commit을 주장하지 않는다.
- 복구 후에도 explicit owned paths만 stage한다. git add -A와 commit -a는 금지한다.

### 1.5 Goal 상태

기준 시각에 goal tool의 thread `019ff607-9116-78a1-9f40-c580c87c0038`은 `active`이며 objective는 현재 개발 문서와 계획을 확인하고 goal을 이어 개발하는 것이다. Shared Knowledge 문서에는 이와 별도의 문서상 goal 상태가 존재할 수 있다.

- xhigh는 매 시작과 재개 시 get_goal을 먼저 호출한다.
- 사용자 요청 없이 goal을 새로 만들거나 기존 goal을 교체, 완료, blocked 처리하지 않는다.
- tool 상태와 문서 상태가 다르면 둘 다 checkpoint에 기록하고 작업 범위를 좁힌다.
- active goal은 이 인계 문서 작성만으로 완료 처리하지 않는다. pause/resume 여부는 사용자나 goal 제어 계층의 명시적 상태를 따른다.

## 2. 권위와 source-of-truth 순위

서로 충돌할 때 다음 순서로 판단한다.

1. 시스템·개발자·사용자의 최신 명시적 지시
2. 현재 경로의 AGENTS.md
3. 현재 source와 frozen schema/hash assertion
4. 현재 격리 테스트로 재현한 결과
5. docs/NOTION_PARITY_ROADMAP.md
6. docs/NOTION_PARITY_MASTER_PLAN.md
7. docs/notion_parity/NP2A_WORKSPACE_SUBSTRATE.md
8. docs/notion_parity/EVIDENCE_POLICY.md
9. docs/NOTION_PARITY_SPEC.md와 과거 테스트 숫자

NOTION_PARITY_SPEC.md는 legacy inventory다. 그 문서의 “완료”나 과거 pass 수를 현재 phase evidence로 사용하지 않는다.

## 3. 상태 표현과 완료 정의

기능과 phase를 다음 네 단계로만 승격한다.

| 상태 | 뜻 | 필요한 증거 |
|---|---|---|
| exists | 코드 또는 도달 가능한 경로가 존재 | source inventory |
| contract-tested | 격리 경로에서 입력·출력·실패·권한 계약 자동 검증 | 재현 명령, 종료 코드, test result hash |
| live-characterized | 승인된 공식 제품 UI에서 기준 조건과 함께 관찰 | date, plan, role, platform, version, locale, redacted evidence |
| differential-passed | 같은 action의 양쪽 결과가 승인된 비교 정책을 통과 | reference/local run hash와 diff report |

금지되는 표현:

- 테스트 파일이 있다는 이유로 contract-tested라고 쓰지 않는다.
- contract test가 통과했다는 이유로 live parity라고 쓰지 않는다.
- 일부 vertical slice를 전체 NP phase 완료라고 쓰지 않는다.
- 운영 DB에 적용하지 않은 migration을 배포 완료라고 쓰지 않는다.
- source tree에 존재하지만 commit provenance가 없는 변경을 landed라고 쓰지 않는다.

현재 공식 판정:

- 전체 phase gate 완료: 0개
- live-characterized 완료: 0개
- differential-passed 완료: 0개
- NP-1: 원자 저장 중심의 첫 contract-tested slice
- NP-2: NP-2A substrate 진행 중
- NP-2B~NP-2E 및 NP-3~NP-11: 최종 gate 미완료

## 4. 현재 구현 스냅샷

### 4.1 NP-0

현재 존재:

- docs/notion_parity_manifest.json
- manifest verifier
- 첫 catalog와 6개 scenario
- evidence redaction/validation policy
- deterministic differential comparator core

현재 부족:

- 전체 NP-0~NP-11 scenario catalog
- contract runner
- 승인된 live capture/action replay
- artifact writer와 dated baseline
- 법무·보안 승인

현재 verifier 관찰:

- 12 phases
- 11 baseline profiles
- 17 features
- 6 Second Brain gates
- catalog 1개, scenario 6개
- reference-conflict 3, reference-only 2, unknown 1

이 숫자는 NP-0 완료 증거가 아니다.

### 4.2 NP-1

현재 존재:

- 59 route contract
- 61 operation semantics
- 15 hard operations
- atomic save
- legacy DB read-only compatibility
- explicit atomic migration
- auth/CSRF/error/fault rollback
- immutable publication snapshot과 Second Brain 회귀

현재 부족:

- legacy PATCH/PUT와 atomic save의 capability negotiation 및 sunset telemetry
- 모든 mutation에 대한 동일 invariant 감사
- 최신 full gate evidence
- live root metadata diff evidence

### 4.3 NP-2A schema chain

| Version | 핵심 목적 | 상태 |
|---|---|---|
| 2026081303 | workspace/IAM catalog, operation authorization context | 격리 contract 기반 존재 |
| 2026081304 | secret-free locator projection | 격리 contract 기반 존재 |
| 2026081305 | immutable parent outbox와 consumer delivery | 격리 contract 기반 존재 |
| 2026081306 | durable healing run/checkpoint | 격리 contract 기반 존재 |
| 2026081307 | source-fenced locator healing apply | 격리 contract 기반 존재 |
| 2026081308 | reasoned quarantine | 격리 contract 기반 존재 |
| 2026081309 | page incarnation attestation과 lineage | current-tree contract와 focused/adjacent evidence 존재 |
| 2026081310 | per-member navigation preference와 page favorites | backend + protected routes contract-tested; UI disabled |
| 2026081311 | canonical page-tree state/node와 lifecycle saga foundation | offline initial materializer contract-tested; runtime disabled |
| 2026081312 | lifecycle lease/fence/operation namespace guards | disabled rename kernel contract-tested |
| 2026081313 | lifecycle-only rename catalog authority/finalizer | atomic changed/no-op finalizer contract-tested; orchestration disabled |

현재 runtime constants:

- workspace schema version: 2026081313
- workspace schema name: notion_workspace_v2_np2c_rename_catalog_finalize
- workspace schema checksum: sha256:6be5b47275cf0dbe0b6b244c3f71672662f8ce648d1efa446208a94488da1637
- workspace actual schema hash: sha256:e971256344e262c59990901b3a6816ecac7be760a31c286f68c5e03d4d491595
- workspace schema object count: 144
- frozen 1312 checksum/actual: sha256:effcfb238406228b79ad780e39c0577de4ac851f881af520c92b627b98b529f6 / sha256:49e388386f2a90ea23fe8861554c50274b127de2ad7aa171e78bb4e9ded074e1
- frozen 1309 checksum/actual: sha256:ecc2a8...127ec / sha256:d79ecc...e06b1

Legacy source incarnation v2:

- version: 2026081302
- name: notion_legacy_page_incarnations_v2
- checksum: sha256:db029078d58d1220fb382a29840cf236149c5191b1c0c83fb26291fe1d8e7903
- object hash: sha256:207c56ae66ae10d0cd0e32753993572df0cbe4add779cb9f93379a459b73f42d

Legacy source change-consumer v3:

- version: 2026081303
- name: notion_legacy_change_consumer_v3
- checksum: sha256:9fc07c846c9e3e1aae98b3a8a8f0507a37fcf06a9ed203e4a5d650c4f3f71495
- object hash: sha256:69a96d7b7c2affd2c0ea83ce80064853d8e12ee8d3f0fbcf27fae3a14f429e6b
- 이 version namespace는 workspace catalog 2026081303과 독립이다.

현재 source tree에 존재하는 1309~1313 구성:

- append-only legacy incarnation history
- legacy v1→v2 guarded migration과 CLI
- catalog 1308→1309 guarded migration과 CLI
- LegacyPageIncarnationEvidence와 attestation DTO
- lineage guard와 deep-health 검증
- explicit admin attestation
- exact operation replay lookup
- offline sentinel/quiesced attestation orchestration
- quarantined binding의 atomic full rebind store API
- exact replay-first, sentinel/quiesced full rebind offline orchestration wrapper
- rebound proof actor derivation helper
- explicit 1309→1310 navigation migration과 dry-run 기본 CLI
- read-time defaults, sidebar preferences와 page favorite store API
- navigation raw/service-context guards, canonical replay와 deep health
- 1311 unmaterialized page-tree/lifecycle saga schema와 offline initial materializer
- 1312 lifecycle lease/fence/namespace guard와 disabled command kernel
- v3-ledger-backed route-unregistered rename source adapter
- 1313 lifecycle-only guard, exact migrator/CLI와 atomic catalog finalizer

현재 존재하지 않거나 활성화하지 않은 범위:

- 운영 legacy/catalog migration
- request/startup automatic migration
- destructive watermark ack
- continuous runtime consumer
- fail-closed resolver cutover
- directory-scan fallback 제거
- runtime file/public resolver cutover
- workspace switcher/UI feature flag
- canary 또는 live rollout
- source adapter와 catalog finalizer를 결합한 exact-sentinel NP2C-3d rehearsal

### 4.4 현재 테스트 증거의 정확한 해석

초기 읽기 전용 감사 중 five-file incarnation selection은 50 passed로 관찰됐다. 이후 H1 수정이 적용된 current tree에서 offline rehearsal까지 포함한 six-file lane을 다시 실행해 54 passed를 확보했다.

- test_migrate_notion_legacy_incarnations.py
- test_migrate_notion_workspace_incarnation.py
- test_notion_workspace_incarnation_attestation.py
- test_notion_workspace_incarnation_orchestration.py
- test_notion_workspace_incarnation_rebind.py

초기 50 passed 뒤 source가 수정됐으므로 그 결과는 이전 snapshot 기준선으로만 유지한다. 위 resumed checkpoint의 54/69/100+2/36/63 결과가 현재 H1/H2 증거다. 따라서:

- 50 passed는 이전 snapshot의 유용한 회귀 기준이다.
- 초기 50 passed를 현재 tree의 최종 green 증거로 사용하지 않는다.
- H1 핵심 수정은 current tree에서 focused와 adjacent regression을 다시 통과했다.
- full rebind offline wrapper 등 H3 이후 변경이 생기면 동일 ladder를 다시 실행한다.

NP2C-3c의 최신 표적 증거는 finalizer `6 passed`, store page-tree+finalizer `11 passed`, strengthened kernel/finalizer/migration subset `17 passed`다. 더 넓은 adjacent lane은 한 실행에서 `68 passed, 2 failed`였고 두 failure는 test가 migration chain을 1311에서 멈춘 문서/fixture drift였으며 수정 뒤 해당 두 test는 `2 passed`했다. 이 문서 인계 시점에는 최종 broad lane과 full migration-chain rehearsal을 다시 직렬 실행해 checkpoint에 고정해야 한다. 따라서 앞선 부분 결과를 broad-current-tree green으로 과장하지 않는다.

### 4.5 historical snapshot과 current 1313 delta

| 파일 | SHA-256 | 비고 |
|---|---|---|
| code_assist_v1/AGENTS.md | 1426602f125062ff89a5e73219b6ce704247bf05bee946de5def3bf754cef5b9 | 읽기 전용 |
| docs/NOTION_PARITY_MASTER_PLAN.md | ff330cf3cfaa458a64129e6837784137a91d8f5456711af2a20654498bcbe26d | H5e/NP2B-0까지 동기화 |
| docs/NOTION_PARITY_ROADMAP.md | bf382fdfa3af06e0f8765a50d1755f314eb493ae3f0d1c65f87daa8ae8a48e92 | H5e/NP2B-0까지 동기화 |
| docs/notion_parity/NP2A_WORKSPACE_SUBSTRATE.md | 5217fef5e7693926bb816a7bbd4b1387ea7690fe87e241614cd7fd57a681c079 | 1309/H5e까지 동기화 |
| docs/notion_parity/NP2B_NAVIGATION_STATE_DESIGN.md | 7ad57a179fa3257106df2336c3cc12c10760f0834953e92202311b0ace7dcd4c | design-only |
| docs/notion_parity/np2b_navigation_contract.json | bb01abd72ca606618684c90eb4b3053968f5716cfbec064807bd2c6edaffa8a3 | implementation/routes/UI 모두 false |
| notion_workspace_store.py | 114e7c7bcf54fc8bbfa9f8b6d4cb320714f1e55b1b1db6d5296f2427250053d0 | current 1309 runtime |
| notion_workspace_projection.py | 0417dee833519d10c231844fca73fd17e477fe7de309e166f33b0e274eeebcaf | proof-actor 분리 반영 |
| notion_workspace_incarnation_orchestration.py | f7dc7dd6b146e43ce074c62ddde6b847ab99f2aec4da12c008913904463bb9d0 | attest/rebind offline wrapper 존재 |
| notion_workspace_incarnation_migration.py | f7151c4fb69994b5a69e4b129ee28276c2c5909aae13223c2e27e129ccb63fe5 | frozen 1308→1309 |
| notion_legacy_incarnation_contract.py | 653a969cd6f35f8a88caa3605ec6fc92db0b5d2d57cbe28b94267ff5d75c5ecb | frozen v2 contract |
| notion_legacy_incarnation_migration.py | 2f04a3e5b60aaf37dac9514ebccb9b70bd3eeef0e06d05189bbe943126cb44f7 | v1→v2 migrator |
| notion_legacy_change_consumer_contract.py | 0e107bfed985957d965b2a9e89000fae16422998326dfb82548cabfa033e5758 | source v3/H5e-0 semantics |
| notion_legacy_change_consumer.py | daafa3d96a8a9ebf9e265fe4d1236aa96dcf405c8d0bd41e0effc20c4163eb0a | no destructive ack API |
| notion_legacy_change_consumer_orchestration.py | 198db1dd1eff5eedc8ff8065c8a2d07246fd89ab9d0a8cd83ea89cc3f77d6526 | superseded remains pending |
| tests/test_notion_workspace_incarnation_rebind.py | 6aeffd0232196ad449811c56965190795f86b9e16c7ee7b88ce4b135e922864d | focused rebind contract |

위 표는 이전 H1/NP2B checkpoint의 historical snapshot이다. 현재 NP2C-3d handoff에서 우선 재검증할 delta는 아래와 같다.

| 파일 | SHA-256 | 비고 |
|---|---|---|
| code_assist_v1/AGENTS.md | 1426602f125062ff89a5e73219b6ce704247bf05bee946de5def3bf754cef5b9 | 읽기 전용, 변경 없음 |
| notion_workspace_store.py | cf12c6766899873262cb81725a09c2ab8ad4559e00f0ff2a6548ae81d20be8a0 | current 1313 runtime/finalized replay helper |
| notion_workspace_page_lifecycle_rehearsal.py | 4215d991e915fb54a42e89ad8b67287d37ba078ea7cb9190653e216f70093c35 | route-unregistered NP2C-3d orchestration |
| tests/test_notion_workspace_page_lifecycle_rehearsal.py | b276ba92683c2be41f96bfe37d5bcb2684c1762c93a28e4ae07f4b2fbb2868c9 | five focused sentinel/response-loss cases |
| notion_workspace_lifecycle_kernel_migration.py | 1b4eb6380223dc831dfaa27579324f854e2e2e4f6e330077dd08e2d0c080acf3 | frozen 1312 target |
| notion_workspace_lifecycle_finalize_migration.py | fdef507581bd44da484fb0a78d7dee82c792cd3d3280f601033ef967c6daf375 | frozen 1312→1313 |
| notion_legacy_page_lifecycle_contract.py | 658da713fe5df1a57087c185deb8a7a4d363ee872e801f276f54216e18958fb8 | v3 rename receipt contract |
| notion_legacy_page_lifecycle.py | a0c81946dc9f1e953d33a12141a430cbb381a4bfcce4249fcf45003d5da99bf0 | route-unregistered source adapter |
| docs/notion_parity/np2c_page_tree_lifecycle_contract.json | ab063ed94299bafad51b4554a164a68ed1c9d8c9548aa30d53ca4f36098b965a | machine contract v9 |
| tools/verify_notion_np2c_page_tree_contract.py | 5e209c2147021c9be3f6b6854150d57b6786e78582bace2a39a0c71aeecaa718 | v9 strict verifier |

이 hash는 인계 snapshot일 뿐 승인된 release manifest가 아니다. xhigh는 시작 시 전부 재계산한다. 문서 자체 hash는 문서와 checkpoint 작성 완료 뒤에만 기록한다.

## 5. 닫힌 1309 차단 이슈와 반드시 유지할 회귀 계약

아래 항목이 닫히기 전에는 migration 실행, continuous consumer, watermark ack, resolver/UI cutover를 시작하지 않는다.

H1 resumed checkpoint에서 B0-1~B0-3 correctness 항목은 닫혔다. 아래 문제/원칙/테스트 목록은 구현 의도와 회귀 기준으로 보존한다. readiness 전수 검사는 explicit maintenance-only이며 runtime request path에는 연결하지 않는다는 성능 경계는 계속 유효하다.

### B0-1. proof actor와 historical bound_by provenance 분리

수정 전 문제:

- bound_by_subject_id는 최초 binding provenance다.
- explicit rebound generation의 source proof actor는 owner-canonical actor일 수 있다.
- 두 값은 같다고 가정하면 안 된다.
- 수정 전 projection capture는 proof_actor를 얻고도 healing DTO의 bound_by_subject_id에 넣었다.
- store apply는 bound_by_subject_id를 immutable provenance fence로 사용하므로 의미가 충돌한다.
- 수정 전 offline incarnation orchestration은 raw bound_by_subject_id를 source actor로 사용했다.

유지할 회귀 원칙:

1. DTO에서 bound_by_subject_id와 proof_actor_subject_id를 별도 필드로 표현한다.
2. catalog binding read는 두 값을 모두 반환한다.
3. bound_by는 provenance/CAS 검증에만 사용한다.
4. proof_actor는 legacy proof와 locator capture/revalidation에만 사용한다.
5. rebound generation에서 proof_actor는 검증된 immutable rebound operation commitment를 통해서만 owner로 결정한다.
6. malformed/missing rebound commitment는 bound_by로 조용히 fallback하지 않고 fail-closed한다.
7. ordinary pre-rebound generation은 proof_actor=bound_by를 유지한다.

필수 회귀 테스트:

- reviewer != owner인 cross-owner rebind
- bound_by는 reviewer로 보존
- proof actor는 owner
- rebound 뒤 capture, revalidate, healing, attestation replay가 owner proof를 사용
- store CAS는 historical bound_by를 계속 검증
- malformed rebound ledger에서 source DB open 0, write 0
- direct capture regression
- archive/quarantine/rebind 이후 두 번째 healing generation

### B0-2. readiness exact validation 통합

수정 전 문제:

- readiness SQL은 operation result와 authorization의 일부 필드만 검사한다.
- deep_health와 action-specific validator보다 약하다.
- extra keys, non-canonical JSON, attestation row 불일치, authorization context drift 등에서 deep_health=false인데 readiness=true가 될 수 있다.
- resolver cutover authority로는 허용할 수 없다.

유지할 회귀 원칙:

1. immutable incarnation operation commitment를 검증하는 shared validator를 하나만 둔다.
2. attested와 rebound action 모두 같은 canonical envelope 규칙을 사용한다.
3. validator는 exact action, workspace, capability, strength, canonical authorization context를 검증한다.
4. result JSON의 exact keyset과 canonical serialization을 검증한다.
5. result.attestation은 현재 attestation row와 정확히 같아야 한다.
6. active generation은 non-null projection commitment가 현재 projection SHA/count와 정확히 같아야 한다.
7. pending binding이 하나라도 있으면 global cutover readiness는 false다.
8. current projection이 이후 변경되면 catalog health를 반드시 corrupt로 만들 필요는 없지만 readiness는 false여야 한다.
9. 결과는 identifier-free count와 boolean만 반환한다.
10. unbounded request-time scan으로 만들지 않는다. maintenance-only bounded/keyset audit 또는 명시적 precomputed readiness 방식을 사용한다.

필수 회귀 테스트:

- malformed canonical JSON
- extra result key
- wrong action/capability/strength/context
- result attestation != row
- same-count projection SHA ABA
- pending attest→later activate
- ordinary projection replacement after attest/rebind
- missing/extra audit 또는 outbox
- current admin이 아닌 caller
- readiness false와 deep_health false/true의 의도된 구분

### B0-3. direct mutation replay의 action-specific validation

수정 전 문제:

- direct attest/rebind mutation의 첫 replay는 generic ledger replay를 사용한다.
- generic replay는 dict JSON 정도만 확인한다.
- 별도 public replay helper보다 약한 검증으로 corrupt ledger를 반환할 수 있다.

유지할 회귀 원칙:

1. exact replay는 계속 quiescence, sentinel, source callback보다 먼저 수행한다.
2. operation 존재 여부를 보기 전에 current workspace-admin authorization을 수행해 oracle을 막는다.
3. direct mutation replay도 action-specific shared validator를 통과한다.
4. request identity, prior binding version, current/rebound version, attestation, projection, authorization context가 모두 exact여야 한다.
5. 같은 operation_id의 다른 request는 conflict다.
6. valid exact replay는 source/sentinel/resolver I/O 0이다.

필수 회귀 테스트:

- corrupt result JSON direct retry
- corrupt authorization direct retry
- corrupt attestation commitment direct retry
- corrupt projection commitment direct retry
- changed request same operation ID
- current IAM revoke 뒤 replay 거부
- valid response-loss retry callback/source I/O 0

### B0-4. 현재 tree 재검증

B0-1~B0-3 수정 전에도 우선 현재 failure를 재현한다. 수정 후에는 아래 H1 verification ladder를 모두 통과해야 한다. 최신 edit보다 이전의 green 결과로 종료하지 않는다.

## 6. 1313 이후 NP-2A/NP2C 실행 계획

### C0. 완료된 continuation — NP2C-3d

NP2C-3c까지 구현된 상태에서 1313 DDL/finalizer를 다시 만들지 않고 아래 한 slice만 수행했으며, 현재-tree evidence와 checkpoint로 닫았다.

1. 새 route나 schema 없이 `notion_legacy_page_lifecycle` v3 source adapter와 `WorkspaceStore.finalize_page_lifecycle_rename_catalog`을 조합하는 새 route-unregistered orchestration module을 만든다.
2. exact operation replay가 sentinel, quiescence, source path, source page read보다 먼저 오게 한다.
3. 새 effect는 explicit rehearsal sentinel, exact catalog/source/user root containment, source quiescence와 catalog command live lease/fence를 모두 요구한다.
4. changed source commit 뒤 응답 손실, catalog finalize commit 뒤 응답 손실, source no-op, source rollback, catalog outbox/audit/operation fault를 각각 재현한다.
5. changed 수렴은 source revision/watermark/source epoch/full-sweep `+1`, 동일 incarnation, catalog binding generation `+1`, complete locator replacement, target tree node refresh, workspace/security `+1`, tree `partial`, durable full-sweep debt를 증명한다.
6. no-op 수렴은 source domain state, binding/tree/workspace version을 그대로 두고 양쪽 immutable operation receipt만 정확히 한 번 남긴다.
7. raw title, path, token, filename, lease token과 page/owner identifier를 aggregate report·audit/outbox·checkpoint에 넣지 않는다.
8. source 또는 catalog ledger가 malformed이거나 source/candidate/workspace/tree/binding/IAM이 drift하면 write 0 또는 manual-audit로 fail closed한다.
9. temp fixtures와 OS test temp만 사용하고 real `user_data`, live catalog/shard, protected route/app/Second Brain 파일을 열지 않는다.
10. 3d checkpoint와 machine contract/doc sync를 마친 뒤 중단한다. create/move/reorder는 NP2C-3e, duplicate/archive/restore는 NP2C-4로 넘긴다.

3d 완료 gate는 source-response-loss와 catalog-response-loss의 양쪽 replay가 source/catalog I/O를 중복하지 않고 같은 canonical identifier-free result로 수렴하며, changed tree가 `complete`로 잘못 남지 않고, 모든 fault injection에서 half-applied cross-DB 상태가 재시도 가능한 durable receipt로 설명되는 것이다. cross-database exactly-once라는 표현은 금지하고 at-least-once/state-idempotent saga로만 기술한다.

이 gate는 임시 exact-sentinel fixture에서 통과했다. 운영 source/catalog, runtime command, route, resolver 또는 watermark ack 증거로 승격하지 않는다.

### C0-current. 현재 정확한 continuation — NP2C-5/v19 reference design complete, live characterization next

NP2C-3e는 create, move, reorder를 하나의 generic mutation으로 묶지 않고 action별 contract로 분리했다. Source-only adapter, frozen 1314 catalog guards, frozen 1315 atomic catalog finalizer와 exact-sentinel rehearsal은 각각 독립 slice로 검증됐다.

1. current runtime은 1317이다. Frozen 1316, 1315, 1314와 1313은 독립 alias와 old migrator target으로 유지되며 1317의 checksum/actual schema/object count는 위 latest checkpoint에 고정한다.
2. source는 frozen legacy v3 immutable ledger에 `legacy.page_lifecycle.create|move|reorder`와 서로 다른 v1 result contract를 사용한다. Source v4는 H5e conditional ack용으로 계속 예약한다.
3. create는 workspace/source owner/actor/caller operation에서 replay-derived `p_<32hex>` proposed identity를 준비하고, parent authority·lock·sibling tail·block/version bounds를 source writer 안에서 재검증한다.
4. move는 resulting parent가 바뀔 때, reorder는 같을 때 사용한다. Same-parent append도 reorder이며 현재 compatibility source가 구조적으로 동일한 요청에도 revisions/watermarks를 갱신하는 사실을 `source_changed`와 `structure_changed`로 분리해 보존한다.
5. pre-write hard bounds는 subtree 1,000/depth 256, old/new sibling scope 각각 10,000, exact receipt union 21,002, aggregate source bytes 256 MiB, parent blocks 5,000, source result JSON 16 MiB다. 초과는 source write 0이다.
6. Frozen 1313의 표현력 부족은 1314의 immutable manifest/effect rows, tree-node order generation, action-specific guards와 exact deep health로 닫혔고, 1315가 그 증거를 atomic catalog state와 operation/audit/outbox에 수렴시키는 finalizer authority를 추가했다.
7. `3e1 source contracts/adapters`, `3e2 frozen 1313→1314 migration/DDL guards`, `3e3 frozen 1314→1315 finalizer/rehearsal`, `4a duplicate/Trash/restore contract design`, `4b route-unregistered source-v3 adapters/file recovery`, `4c frozen 1315→1316 lifecycle-subtree schema/guards`, `4d frozen 1316→1317 atomic finalizer/exact-sentinel rehearsal`, `4e source-ledger recovery/adversarial audit`와 `5 official Archive reference design`까지 완료됐다. Archive와 Trash는 별개이며 current `archive_page`는 Trash compatibility라는 사실을 유지한다.
8. 4e는 source acknowledgement/finalizer loss replay, three-phase rollback, 11,004 exact bound/11,005 refusal, redaction과 deep-health를 machine contract v18과 별도 checkpoint로 고정했다. 5는 source v4 예약을 보존한 candidate v5/workspace 1318, active binding/locator와 canonical archived state를 machine contract v19로 고정했다. 둘 다 runtime evidence가 아니다.
9. NP2C-5a matrix v1/runbook와 authorization/preparation/acceptance verifiers는 준비됐지만 actual authorization, live execution과 accepted bundle은 없다. 다음 실행 대상은 `NP2C-5a-approved-business-enterprise-live-characterization`이며 current-time preflight 통과 뒤 승인된 fixture와 role matrix로 unknown을 관찰해 reviewed decision으로 바꾸기 전에는 source v5, workspace 1318, archive adapter/finalizer를 구현하지 않는다.
10. official Archive/unarchive implementation, public duplicate, route/UI/runtime resolver, live migration, continuous worker, destructive watermark ack와 purge는 후속 별도 gate 전까지 금지다.

### H0. 인계 bootstrap과 snapshot

목적: 다른 개발을 덮어쓰지 않고 현재 truth를 다시 확정한다.

작업:

1. AGENTS.md 재독
2. get_goal 확인
3. 보호 경로와 owned file allowlist 선언
4. 기준 hash/mtime 재계산
5. frozen checksum assertion import
6. 현재 focused test baseline 실행
7. pre-existing failure와 새 failure 분리
8. checkpoint 작성

종료 gate:

- drift가 없거나 drift를 반영한 새 감사가 완료
- 수정 파일 소유권이 명확
- live path 접근 0
- current baseline 결과가 기록됨

### H1. 1309 correctness freeze

범위:

- B0-1 proof actor/provenance 분리
- B0-2 readiness exact validator
- B0-3 direct replay exact validator
- focused tests

제안 owned-file allowlist는 아래에서 시작하되, 실제 수정 전 hash 재검증과 소유권 확인을 다시 한다.

- notion_workspace_store.py
- notion_workspace_projection.py
- notion_workspace_incarnation_orchestration.py
- notion_workspace_healing.py는 DTO propagation이 실제로 필요한 경우에만
- tests/test_notion_workspace_incarnation_attestation.py
- tests/test_notion_workspace_incarnation_rebind.py
- tests/test_notion_workspace_incarnation_orchestration.py
- tests/test_notion_workspace_projection.py
- tests/test_notion_workspace_healing_apply.py

H1에서는 migration module, DDL version, route, app host, Shared Knowledge 파일을 allowlist에 넣지 않는다.

금지:

- DDL 또는 schema version 변경을 편의상 수행
- resolver/UI 연결
- migration apply
- protected 파일 수정

완료 gate:

- latest tree focused suite green
- attestation/rebind/readiness/deep-health가 동일 validator를 공유
- replay-before-I/O 보장
- identifier-free result
- source proof actor와 bound_by 각각 정확한 의미
- security reviewer P0/P1 0

### H2. 문서·source traceability 동기화 — 1313까지 반복 적용

각 schema/slice가 끝난 뒤 다음 기존 문서를 현재 frozen chain으로 갱신한다. 현재 target은 1313이다.

- docs/NOTION_PARITY_MASTER_PLAN.md
- docs/NOTION_PARITY_ROADMAP.md
- docs/notion_parity/NP2A_WORKSPACE_SUBSTRATE.md

함께 추가할 자동 검증:

- runtime SCHEMA_VERSION과 문서 current schema 일치
- current 1313 및 frozen predecessor checksum/actual hash literal 일치
- legacy incarnation version/checksum/object hash 일치
- migration chain 1303→1313 누락 0
- “source incarnation과 rebind가 미래”라는 stale 문구 0

NOTION_PARITY_SPEC.md는 다음 중 하나로 명확히 정리한다.

- legacy inventory 배너를 추가하고 상태 근거로 사용 금지
- 또는 scenario-based status로 재작성

기존 Knowledge STATUS나 traceability는 수정하지 않는다. 관련성이 생기면 해당 owner에게 STATUS_DELTA만 전달한다.

### H3. full rebind offline orchestration — 격리 구현 완료, 운영 미적용

현재 store direct API 위에 exact-sentinel, quiesced, pre-source-authorized wrapper가 존재한다. 아래 계약은 재구현 목록이 아니라 이후 변경이 깨뜨리면 안 되는 회귀 기준이다.

계약:

1. exact replay helper가 첫 번째다.
2. unknown operation은 admin authorization 뒤에만 None을 반환한다.
3. 새 effect는 quiescence를 첫 filesystem gate로 요구한다.
4. sentinel 아래 exact catalog/source/user root만 허용한다.
5. owner-canonical v2 receipt, proof, complete locator를 한 pinned immutable snapshot에서 캡처한다.
6. callback은 source-only exact tuple revalidation을 수행한다.
7. catalog CAS는 callback 뒤 다시 수행한다.
8. q(N)→pending(N+1)→active(N+1), projection, attestation, operation, audit, outbox, workspace/security bump를 한 transaction에 묶는다.
9. historical bound_by와 placement/teamspace/created_at은 보존한다.
10. report에는 workspace/owner/page/path/nonce/digest/token/filename을 넣지 않는다.

필수 failure:

- source incarnation drift
- source fence drift
- binding/workspace version drift
- admin revoke
- lineage regression
- projection collision
- audit/outbox/operation fault
- response loss 뒤 replay

현재 격리 gate:

- wrapper focused test
- no source I/O replay
- atomic rollback
- readiness/deep_health true
- rebound 뒤 다음 healing/capture까지 end-to-end green

### H4. migration-chain 복원 snapshot rehearsal — 격리 완료, live restore 미승인

격리 fixture에서 다음을 검증했다. xhigh는 입력 hash가 바뀌었으면 같은 범위를 다시 실행하며, 이를 운영 restore 증거로 승격하지 않는다.

- workspace 1303→1304→1305→1306→1307→1308→1309→1310→1311→1312→1313
- legacy change tracking v1→incarnation v2
- frozen source/target checksum과 actual object hash
- fresh 1313과 migrated 1313 object equality
- retained 1312 restore point 뒤 1313 재적용과 current deep health
- no fabricated attestation
- existing active는 unattested readiness false
- explicit rebind/attest 뒤에만 readiness true
- backup/restore drill
- sidecar/path/symlink/hardlink/TOCTOU
- ambiguous commit outcome convergence

운영 apply는 하지 않는다.

### H5. watermark consumer coordination

이 단계 전에는 destructive ack를 금지한다.

현재 완료된 H5a foundation:

- existing durable healing lease/source fence 재사용
- source epoch/full-sweep/binding generation에 결속된 page-effect operation identity
- immutable effect result에 identifier-free exact source receipt 영속화
- effect-commit/checkpoint-loss 재시도의 source-I/O-free replay 수렴
- exact receipt/result tamper fail-closed

H5b에서 source-local durable pass/work/operation contract, bounded keyset materialization, lease reclaim, owner/pass counter와 explicit v2→v3 migration까지 구현했다. H5c에서는 disabled/offline catalog effect→source terminal 수렴, 양쪽 response-loss replay, exact owner-fenced full-sweep completion 증거와 required-sweep next-pass gate를 구현했다. H5d는 `applied` terminal work가 참조한 모든 explicit workspace completed-run set, identifier-free lag, process restart replay와 no-ack repeated-pass duplicate 수렴을 검증했다. H5e 감사 뒤 `superseded` effect는 source-terminal이 아니라 pending retry debt로 남고 completion/ack 대상에서 제외된다. 명시한 workspace에 없는 source work도 pending으로 남으며, source-generation conditional DELETE/ack는 별도 승인 전까지 존재하지 않는다. H5a~H5e 설계만으로 continuous runtime rollout 또는 ack 완료를 주장하지 않는다.

권장 순서:

1. owner별 durable lease/fence/row-version state
2. source epoch/full-sweep generation 고정
3. page receipt를 durable inbox/work row로 materialize
4. effect replay와 source terminal receipt
5. crash-after-effect-before-source-terminal/checkpoint 수렴
6. full sweep generation 변경 시 전체 binding pass와 completion evidence
7. identifier-free counters와 lag telemetry
8. H5e 설계·machine contract review
9. 별도 승인 뒤 source v4 no-ack schema/migrator
10. 별도 review 뒤 explicit offline source-generation conditional ack
11. 그보다 뒤에 continuous scheduling 검토

중요 invariant:

- page_id keyset만 영구 cursor로 쓰면 작은 page ID의 재발행을 잃을 수 있다.
- ack는 page_id만으로 삭제하지 않는다.
- source file identity, source_epoch, page change_generation/mask/state, full_sweep_generation, incarnation과 receipt digest를 모두 조건에 포함한다.
- operation ID에는 owner/page뿐 아니라 source identity/epoch/full sweep, incarnation, receipt, catalog effect와 completion operation을 포함한 domain-separated hash를 사용한다.
- 같은 owner shard의 destructive ack consumer는 하나로 고정한다. fan-out은 durable per-consumer inbox 없이는 허용하지 않는다.
- ack는 source mutation이 아니므로 source epoch/full-sweep generation을 증가시키지 않는다.
- continuous consumer는 먼저 shadow-only로 운용한다.
- 수렴과 lag가 검증되기 전에 destructive ack를 활성화하지 않는다.

workspace 1309는 그대로 유지한다. source ack schema는 frozen source v3를 수정하지 않고 candidate source v4 `2026081304` forward migration으로만 설계한다. 정확한 계약은 `docs/notion_parity/H5E_CONDITIONAL_ACK_DESIGN.md`를 따른다.

### H6. fail-closed resolver cutover

진입 조건:

- 1309 correctness freeze
- full rebind operational flow
- unattested active count 0
- pending count 0
- projection commitment exact
- watermark consumer convergence
- restore drill
- zero-leak corpus

resolver 원칙:

- exact workspace, actor, owner, page, generation으로 조회
- catalog IAM과 projection membership을 먼저 확인
- legacy exact-owner row를 재검증
- public raw token은 즉시 digest하고 constant-time 원 token 확인
- file은 opaque file ID와 membership, exact path containment
- miss에서 directory scan fallback 금지
- catalog unavailable, stale, ambiguous, unattested는 fail-closed
- canary leakage/denial telemetry는 identifier-free

순서:

1. offline shadow
2. Notion host opt-in shadow
3. page list/GET canary
4. file canary
5. public canary
6. session/search/AI consumer
7. directory scan 제거

### H7. workspace UI와 운영 rollout

resolver gate 뒤에만 진행한다.

- workspace switcher
- draft flush
- stale-response fencing
- route-level current workspace context
- feature flag default off
- kill switch
- one synthetic workspace
- one canary
- staged rollout

UI는 app_code.py나 Shared Knowledge shell에 우회 등록하지 않는다. 현재 Notion host 경계의 명시적 owner와 route contract를 따른다.

### N1. 완료된 무파괴 구현: NP2B-1/2/3 workspace navigation vertical slices

H5e destructive ack는 승인되지 않았다. NP2B-1은 source shard와 resolver를 건드리지 않는 catalog
backend로, NP2B-2는 Notion host protected route로, NP2B-3은 server capability와 kill switch를 모두
요구하는 default-off UI로 분리 구현했다. 실제 환경 flag, live migration, Recents와 resolver는 켜지지
않는다. 별도 설계 원문과 machine contract는 다음 두 파일이다.

- `docs/notion_parity/NP2B_NAVIGATION_STATE_DESIGN.md`
- `docs/notion_parity/np2b_navigation_contract.json`

#### N1.1 시작 전 소유권과 frozen lineage

후보 owned-file allowlist:

- `notion_workspace_store.py`
- `notion_workspace_incarnation_migration.py` — 기존 1308→1309 migrator를 runtime 1310과 분리해 frozen 1309 target을 계속 재현하는 최소 수정만 허용
- 새 `notion_workspace_navigation_migration.py`
- 새 `tools/migrate_notion_workspace_navigation.py`
- 새 focused store/migration test 파일
- 기존 store/migration test 중 current-runtime version assertion을 교정해야 하는 정확한 파일만 추가 승인

기본 금지 파일:

- `routes_notion_workspace.py`
- `notion_workspace_pages.py`
- `app_code.py`
- auth/config/data-path/Second Brain/Shared Knowledge 파일

시작 순서:

1. 위 파일의 size, mtime UTC, SHA-256을 기록한다.
2. current 1309 focused baseline을 serial로 실행한다.
3. `notion_workspace_store.py`의 current runtime checksum과 actual schema hash가 이 문서의 1309 값과 같은지 확인한다.
4. current 1309 전체 SQL을 `_SCHEMA_1309_SQL`과 literal checksum으로 먼저 동결한다.
5. 기존 1308→1309 migrator가 mutable runtime constants를 참조하지 않도록 분리한다.
6. 그 뒤에만 candidate 1310 extension을 추가한다.

#### N1.2 candidate 1310 데이터 모델

정확한 SQL 이름과 checksum은 구현·독립 감사 뒤 frozen한다. 최소 의미 모델은 다음 세 집합이다.

1. `workspace_navigation_states`
   - key: `(workspace_id, subject_id)`
   - 독립 CAS: `preference_version`, `favorite_version`
   - `sidebar_width_px`, `sidebar_collapsed`
   - 생성/수정 시각과 last actor/operation correlation
2. `workspace_sidebar_section_preferences`
   - key: `(workspace_id, subject_id, section_key)`
   - section allowlist: `favorites`, `recents`, `teamspaces`, `shared`, `private`
   - display order, hidden, collapsed, bounded item limit
   - 같은 subject 안의 order uniqueness
3. `workspace_page_favorites`
   - key: `(workspace_id, subject_id, source_owner_subject_id, source_page_id)`
   - stable ordinal과 operation correlation
   - page binding의 mutable row version이 아니라 generation-stable owner/page identity를 사용

이 slice에는 recents row나 view-history write를 넣지 않는다. 고빈도 recents를 immutable operation/audit/outbox와 일대일로 기록하면 retention과 비용이 커지므로 coalescing·retention 결정 전에는 schema/API를 frozen하지 않는다.

DDL 불변식:

- current active workspace member만 자신의 navigation state를 소유한다.
- guest와 non-member는 row 존재를 추론할 수 없어야 한다.
- raw SQL insert/update/delete는 service context와 exact operation correlation 없이는 fail-closed한다.
- mutation의 operation, audit, outbox payload는 canonical·secret-free·exact action/target이어야 한다.
- preference/favorite version은 서로 영향을 주지 않는다.
- navigation 변경은 workspace content `row_version` 또는 `security_epoch`을 증가시키지 않는다.
- unknown/partial 1309, future schema, checksum/object drift는 migration refusal이다.

#### N1.3 route-unregistered store API

첫 slice의 public host route는 0개다. store surface만 다음 의미로 제공한다.

- `get_navigation_state(actor, workspace)`
  - current member만 허용
  - source DB open과 directory scan 0
  - read-time default를 반환하되 단순 GET이 canonical row를 생성하지 않음
  - favorite 대상의 current catalog visibility를 매번 재확인
  - invisible favorite는 identifier/count/ordinal leak 없이 생략하고 visible 결과를 dense reindex
  - title/content/path/public token을 결과에 포함하지 않음
- `replace_sidebar_preferences(..., expected_preference_version, operation_id)`
  - exact replay first, current membership reauthorization, preference CAS
  - section set/order/limit을 한 transaction에 교체
- `add_page_favorite(..., expected_favorite_version, operation_id)`
  - current catalog visibility를 writer transaction에서 재확인
  - duplicate logical favorite는 idempotent 또는 명시적 no-op result로 고정
- `remove_page_favorite(..., expected_favorite_version, operation_id)`
  - 대상 page가 이후 invisible이 되어도 caller-owned favorite를 제거할 수 있어야 함
  - 제거를 위해 source DB나 target content를 열지 않음

모든 mutation은 한 `BEGIN IMMEDIATE` 안에서 domain row, immutable operation ledger, audit, outbox를 함께 commit한다. callback/network/file/source 작업은 transaction 안에 넣지 않는다. exact replay는 현재 membership을 먼저 재검증하고 같은 operation ID의 다른 request hash는 conflict로 거부한다.

#### N1.4 1309→1310 migration 계약

- source는 exact frozen 1309 version/name/checksum/object set만 허용
- target checksum과 actual schema hash는 literal로 고정
- dry-run은 byte/read-only
- apply는 explicit CLI에서만, default dry-run
- exact path, link/sidecar, backup, quiescence, integrity/FK, ambiguous outcome 규칙은 기존 hardened migration contract를 재사용
- fresh 1310과 migrated 1310의 `sqlite_master` object set/hash가 같아야 함
- 기존 workspace/binding/projection/attestation/healing/outbox row는 byte-level logical fingerprint가 보존되어야 함
- migration은 navigation row를 사용자 대신 fabricate하지 않음
- running healing/outbox state를 임의로 완료하거나 변경하지 않음
- request/startup import와 live apply는 계속 금지

#### N1.5 focused test matrix

최소 positive:

- fresh 1310 bootstrap와 deep health
- exact frozen 1309→1310 migration, already-current convergence
- default navigation read, preference update/replay, favorite add/remove/replay
- preference version과 favorite version 독립
- hidden favorite omission과 dense reindex
- invisible favorite removal
- operation/audit/outbox exact correlation

최소 negative/fault:

- non-member/guest/revoked membership, cross-workspace actor
- stale preference/favorite version
- changed request under same operation ID
- malformed section/order/width/limit, duplicate order
- hidden target add, quarantined/pending/unattested binding 정책 위반
- raw SQL without service context, direct tamper, missing/extra ledger trio
- operation/audit/outbox/domain fault 각각에서 전체 rollback
- migration source/checksum/object/path/backup/quiescence/fault/ambiguous outcome
- navigation read에서 source resolver/open 및 directory scan trap 0회
- result/audit/outbox에 page title/content/path/token/nonce/filename 0

검증 순서:

1. new migration focused tests
2. new navigation store tests
3. existing workspace store/incarnation/deep-health tests
4. workspace migration chain through 1310
5. pages/routes/auth isolation regression — route count는 이 slice에서 그대로여야 함
6. Notion machine contracts와 Second Brain hard regression
7. source/doc hash와 checkpoint 고정

#### N1.6 역사적 NP2B-1 종료 gate와 현재 후속 상태

NP2B-1 checkpoint 시점에는 `backend_schema_implemented=true`, `backend_store_api_implemented=true`까지만
허용했고 아래 값이 false였다.

- `routes_enabled`
- `ui_enabled`
- `recents_write_enabled`
- `resolver_cutover_enabled`
- `live_migration_applied`

그 stop point는 지켜졌고 이후 NP2B-2 route와 NP2B-3 default-off UI를 별도 checkpoint로 진행했다.
현재도 `ui_enabled`, `recents_write_enabled`, `resolver_cutover_enabled`, `live_migration_applied`는 false다.

## 7. 전체 NP-0~NP-11 실행 계획

### 7.1 전체 상태 표

| Phase | 현재 최고 상태 | 완료된 기반 | 남은 핵심 gate |
|---|---|---|---|
| NP-0 | 부분 contract-tested | manifest, verifier, comparator, evidence policy, 6 scenarios | full taxonomy, contract runner, live capture, artifacts, legal/security |
| NP-1 | 첫 slice contract-tested | atomic save, route/auth/error/fault/publication | negotiation/sunset, all-mutation audit, latest gate evidence |
| NP-2 | NP-2A/NP2C 진행 중 | catalog/IAM, frozen migrations through 1317, NP2B default-off navigation, NP2C rename saga rehearsal, 3e1 source adapters, 3e2 structural guards, 3e3 atomic offline finalizer/rehearsal, 4a~4e duplicate/Trash/restore와 independent audit, 5 official Archive reference design | NP2C-5a live characterization와 Archive implementation, NP-2A runtime cutover, NP-2B~E completion |
| NP-3 | exists 일부 | legacy editor surface와 JS tests | canonical block/command, IME, clipboard, history, a11y |
| NP-4 | exists 일부 | embedded database UI | canonical data source/properties/formula/views |
| NP-5 | exists 일부 | comments/polling/conflict paths | realtime convergence/reconnect/revoke/load |
| NP-6 | exists 일부 | search/backlink 관련 경로 | ACL-aware index/corpus/recovery/workflows |
| NP-7 | exists 일부 | public/form surface | public subtree/Sites/threat/crawl/cache |
| NP-8 | responsive exists 일부 | CSS/web surface | native/offline/device/no-loss/a11y |
| NP-9 | plan | adapter boundaries | automation/OAuth/webhook/provider/legal |
| NP-10 | 일부 AI route 외 plan | current AI surface | Agents/citation/action/Enterprise/compliance |
| NP-11 | plan | migration principles | scope freeze, restore, pilot, full differential, approval |

### 7.2 NP-0 — 검증 플랫폼

작업:

- catalog schema를 NP-0~NP-11 전체로 확장
- 필수 scenario skeleton과 source/plan/role/platform/date 연결
- repository contract runner
- normal UI action script format
- approved browser live capture
- deterministic redaction
- evidence writer
- state/keyboard/focus/a11y/error/geometry comparator
- reference-conflict 처리
- release-event rebaseline

완료 gate:

- taxonomy 누락 0
- redaction leak 0
- runner repeatable
- source/date/plan/role/platform/locale 누락 0
- legal/security approval
- live evidence 없는 differential claim 0

주의:

- 공식 API, private endpoint, scraping으로 복제하지 않는다.
- raw DOM/network/cookie/token을 repo에 저장하지 않는다.
- live evidence artifact는 승인된 encrypted 외부 저장소에 둔다.

### 7.3 NP-1 — 런타임·호환 경계

잔여 작업:

- legacy mutation과 atomic mutation capability negotiation
- legacy endpoint sunset telemetry와 종료 조건
- 모든 mutation의 actor/auth/idempotency/CAS/bounded transaction
- route/service/repository ownership audit
- health/readiness/metrics
- crash/fault matrix
- live root metadata diff

완료 gate:

- new/legacy fixture 모두 통과
- route/auth/error/fault/rollback
- Second Brain hard regression
- live root write 0
- request-time schema mutation 0

### 7.4 NP-2A — canonical locator와 cutover

상세 순서는 H0~H7을 따른다.

완료 gate:

- 1303→1315 및 legacy v1→v2→consumer v3 exact migration chain
- verified backup/restore
- attestation/rebind correctness
- unattested/pending 0
- destructive ack의 generation-conditional correctness
- directory scan 0
- page/file/public resolver fail-closed
- revoke 즉시 반영
- identifier leak 0
- feature flag와 kill switch

### 7.5 NP-2B — sidebar/tree/favorites/recents

NP2B-1은 workspace `2026081310`의 per-member navigation state, sidebar/section preference와
page favorite backend를 구현했고 NP2B-2는 protected route overlay를 구현했다. NP2B-3은 UI 코드를
연결했지만 runtime default와 실제 환경 flag는 off다. 다음 순서는 다음과 같다.

1. [x] NP2B-1: frozen 1309→1310 migration과 store API
2. [x] NP2B-2: Notion workspace route overlay와 strict auth/CSRF contract
3. [x] NP2B-3: default-off feature UI, stale response/focus/a11y
4. NP2B-4: audit/outbox retention 결정 뒤 Recents
5. NP2B-5: canonical page-tree command와 drag/keyboard integration

작업:

- server-backed workspace switcher
- Favorites, Shared, Private, Teamspaces
- collapse/resize
- create/rename/move/duplicate
- drag target와 optimistic response fencing
- breadcrumb
- recents/view history
- keyboard tree navigation
- focus restore
- a11y names/state

완료 gate:

- current member only, guest/non-member/revoked 404
- navigation snapshot source DB open/directory scan 0
- hidden favorite count/ordinal leak 0
- preference/favorite version 독립 CAS와 replay
- every mutation operation/audit/outbox atomic
- tree mutation atomic/CAS
- cycle/cross-workspace 거부
- stale drag response 무시
- reload persistence
- keyboard/a11y differential

### 7.6 NP-2C — lifecycle/Trash

현재 완료된 foundation은 1311 tree/saga, 1312 lease/fence guards, NP2C-3b v3 source rename adapter, 1313 atomic catalog finalizer, NP2C-3d exact-sentinel rename rehearsal, NP2C-3e1 create/move/reorder v3 source adapter, NP2C-3e2 frozen 1314 structural guards, NP2C-3e3 frozen 1315 atomic catalog finalizer/exact-sentinel rehearsal, NP2C-4a duplicate/Trash/restore contract design, NP2C-4b route-unregistered source-v3 adapters/file recovery, NP2C-4c frozen 1316 lifecycle-subtree schema/guards, NP2C-4d frozen 1317 atomic catalog finalizer/exact-sentinel rehearsal, NP2C-4e independent source-replay/adversarial audit와 NP2C-5 official Archive reference design까지다. 정확한 다음 slice는 승인된 `NP2C-5a` Business/Enterprise live characterization이다.

작업:

- NP2C-3d rename end-to-end response-loss rehearsal 완료·회귀 유지
- NP2C-3e design/machine contract 완료·회귀 유지
- NP2C-3e1 create/move/reorder legacy-v3 source adapters 완료·회귀 유지
- NP2C-3e2 frozen 1313→1314 schema/guards 완료·회귀 유지
- NP2C-3e3 atomic catalog finalizers와 sentinel rehearsal 완료·회귀 유지
- NP2C-4a duplicate/Trash/restore design과 machine contract v14 완료·회귀 유지
- NP2C-4b source-v3 adapters/file recovery와 machine contract v15 완료·회귀 유지
- NP2C-4c frozen 1316 schema/guards와 machine contract v16 완료·회귀 유지
- NP2C-4d frozen 1317 atomic catalog finalizers/exact-sentinel rehearsal와 machine contract v17 완료·회귀 유지
- NP2C-4e independent source-replay/rollback/bounds/redaction/deep-health audit와 final evidence checkpoint 완료·회귀 유지
- archive/deactivate
- restore
- permanent delete eligibility와 승인
- subtree behavior
- orphan file/projection policy
- recovery and audit

완료 gate:

- destructive failure partial write 0
- restore identity 보존
- permission revoke와 lifecycle의 결합
- purge는 별도 admin-only 정책
- tree/UI/API/DB recovery differential

### 7.7 NP-2D — unified capability resolver

작업:

- member/guest/group/teamspace/page/public grant
- broadest access 계산
- explicit deny/revoke
- every read/write/search/export/AI/public path의 동일 resolver
- permission cache invalidation
- zero-leak oracle matrix

완료 gate:

- tenant/role canary leakage 0
- revoke 즉시
- 404/403 정책 일관
- background job도 동일 authority
- direct repository bypass 0

### 7.8 NP-2E — public subtree

작업:

- public root/subtree snapshot
- inherited options
- child lifecycle
- file/public token resolver
- duplicate digest/collision
- cache/crawl/unpublish

완료 gate:

- anonymous threat corpus
- hidden metadata leak 0
- unpublish 즉시
- subtree change invalidation
- file/token secret redaction

### 7.9 NP-3 — Document/Editor Core

작업:

- canonical block/rich-text schema
- command model
- selection/caret/focus
- slash/inline menus
- drag/multi-select
- clipboard/sanitization
- undo/redo
- Korean IME
- autosave/history/conflict
- complex blocks/media/layout
- unsupported block round-trip/degraded state

완료 gate:

- block corpus
- IME composition loss 0
- clipboard security
- crash/reload
- stale conflict
- keyboard/focus/a11y
- publication snapshot
- differential pass

### 7.10 NP-4 — Database Engine

작업:

- canonical data source
- typed properties
- relation/rollup/formula
- templates/layout
- table/board/list/gallery/calendar/timeline
- filter/sort/group
- saved view and view-local state
- concurrency and permissions

완료 gate:

- property/formula/rollup corpus
- view isolation
- query determinism
- concurrent mutation CAS
- permission matrix
- import/export round-trip

### 7.11 NP-5 — Realtime/Collaboration

transport 설계는 NP-4와 병렬 가능하지만 content mutation 활성화는 NP-3/NP-4 command 안정화 뒤다.

작업:

- operation/event model
- reconnect/resume
- out-of-order/duplicate
- presence
- comment/thread/reaction
- mention/reminder
- Inbox/notification
- permission revoke fencing

완료 gate:

- multi-client convergence
- stale overwrite 0
- disconnect/reconnect
- revoke 즉시
- load and backpressure
- cross-process tests

### 7.12 NP-6 — Discovery/Workflows

작업:

- ACL-aware search/index
- backlinks
- mentions/reminders
- templates/buttons
- wiki/home/feed
- chart/dashboard/map/print
- index rebuild/recovery

완료 gate:

- precision/recall corpus
- private canary leak 0
- index lag/rebuild
- permission invalidation
- keyboard/a11y

### 7.13 NP-7 — Forms/Sharing/Sites

작업:

- form builder/validation/submit
- share lifecycle
- public renderer/files
- Sites-like renderer
- SEO/domain/analytics
- abuse/rate/quota

완료 gate:

- anonymous threat matrix
- duplicate/replay protection
- file validation
- revoke/unpublish/cache invalidation
- crawler behavior
- plan/role differential

Core Web candidate는 NP-0~NP-7의 채택 범위가 모두 gate를 통과해야 한다.

### 7.14 NP-8 — Native/Offline 별도 트랙

작업:

- responsive web
- desktop/mobile platform differences
- offline manifest/cache/queue
- sync/conflict
- device encryption/storage
- device lab
- touch/mobile a11y

완료 gate:

- device matrix
- no-loss fault test
- sync observability
- conflict UX
- offline permission revoke policy

### 7.15 NP-9 — Automation/Connections

provider별 독립 adapter로 진행한다.

작업:

- automation engine
- triggers/actions
- webhook
- OAuth
- retry/idempotency
- secret lifecycle
- provider sandbox
- sync database
- developer platform

완료 gate:

- credential leak 0
- webhook replay
- retry chaos
- provider rate limit
- deletion/revoke
- legal/privacy approval

### 7.16 NP-10 — AI/Agents/Enterprise

작업:

- permission-scoped AI read/write/Q&A
- citations
- proposed actions와 human approval
- Meeting Notes
- Agents
- HTML sandbox
- SAML/SCIM/DLP/audit/eDiscovery

완료 gate:

- permission/citation/action eval
- red-team prompt/context leak 0
- destructive action human approval
- retention/privacy
- Enterprise role/audit matrix

고유 모델의 exact wording은 parity 대상이 아니다.

### 7.17 NP-11 — Migration/Pilot/Final

진입 전 채택 범위를 동결한다.

작업:

- immutable inventory
- backup/restore drill
- migration/reconciliation
- internal rehearsal
- canary
- allowlist
- one workspace
- staged rollout
- telemetry/support
- rollback/kill switch
- legal/brand/operations approval

완료 gate:

- 채택된 필수 scenario differential-passed
- unresolved conflict 0
- restore/no-loss
- security/a11y/performance
- pilot soak
- operations/legal approval

장기 dual-write는 금지한다.

## 8. 정규화된 의존성 DAG

~~~text
NP-0 → NP-1 → NP-2A → (NP-2B || NP-2C) → NP-2D → NP-2E
     → NP-3 → NP-4 → NP-5 → NP-6 → NP-7 → Core Web gate

NP-8: NP-1~NP-5 이후 별도 native/offline track
NP-9: NP-1 + NP-2D + NP-4 + NP-5 + NP-7 이후 provider별 track
NP-10: 채택된 IAM/data/automation/platform 기반 이후
NP-11: excluded/separate가 아닌 채택된 필수 scope 전체 이후
~~~

병렬화 가능:

- NP-0 CI lane은 모든 phase와 병렬로 계속 강화
- H1 correctness와 H2 문서 traceability는 파일 소유권이 분리되면 병렬 가능
- NP-2B UI interaction과 NP-2C lifecycle backend는 canonical tree contract 뒤 병렬 가능
- NP-5 transport 설계는 NP-4와 병렬 가능
- NP-6 read-only search 실험과 NP-7 public threat corpus는 capability contract를 우회하지 않는 범위에서 병렬 가능

병렬화 금지:

- 같은 shared file 동시 편집
- DDL과 runtime API가 frozen hash 합의 없이 병렬 변경
- destructive ack와 effect correctness 검증
- resolver cutover와 readiness 정의 변경
- UI activation과 server authorization 미완료

## 9. Vertical slice 완료 정의

각 slice는 다음을 함께 끝낸다.

- bounded context와 owner
- schema/DTO
- service transaction
- API/route
- UI interaction
- migration
- idempotency/replay
- negative/security
- accessibility
- telemetry
- backup/restore/rollback
- Second Brain regression
- evidence/status

happy path만 있거나 UI만 보이는 상태는 완료가 아니다.

## 10. 보안·데이터·transaction 불변식

- canonical mutation, operation ledger, audit, outbox는 한 transaction이다.
- 외부 source, network, LLM, OCR, file copy는 transaction 밖에서 수행한다.
- commit 직전에 IAM, version, source fence를 다시 확인한다.
- 모든 retryable mutation은 caller operation_id를 가진다.
- exact replay는 external I/O보다 먼저지만 현재 IAM을 재검증한다.
- 같은 operation ID의 다른 request hash는 conflict다.
- raw path, filename, public token, lease token, nonce, content는 result/audit/outbox/report에 넣지 않는다.
- source DB와 catalog의 cross-DB atomicity를 주장하지 않는다.
- source quiescence와 exact revalidation으로 경계를 명시한다.
- request/startup path에서 schema migration/repair를 수행하지 않는다.
- unknown schema/checksum/object는 manual audit로 fail-closed한다.
- projection은 canonical source가 아니다.
- directory scan은 authorization/resolver의 정상 fallback이 아니다.
- admin operation도 cross-tenant existence oracle이 되면 안 된다.
- raw SQL invariant는 service invariant보다 약하면 안 된다.

## 11. Migration·backup·restore 계약

모든 forward migration:

- exact source version/name/checksum/object allowlist
- literal frozen target checksum/actual hash
- dry-run default
- exact resolved path
- symlink/junction/hardlink/ADS 거부
- sidecar policy
- quiescence confirmation
- verified single-link backup
- integrity/FK/logical fingerprint
- single transaction 또는 reviewed copy-on-write atomic replace
- fault injection rollback
- ambiguous commit/replace convergence
- postcondition exact target validation
- no live apply without explicit user authority

운영 전 필수:

1. exact approved catalog/shard inventory
2. quiesce
3. application-consistent backup
4. restore drill
5. dry-run checksum record
6. one canary
7. smoke
8. soak
9. staged rollout

rollback:

- reverse DDL을 현장에서 만들지 않는다.
- first write 전에는 verified backup restore
- first write 후에는 writes 중지, incident DB 보존, forward fix 또는 reviewed restore
- operation/audit evidence 보존
- Second Brain 데이터를 암묵적으로 함께 rollback하지 않는다.

## 12. 테스트 실행 harness

### 12.1 기본 실행 규칙

- cwd는 G:\AI_Agent\DEMOS_CODEX
- interpreter는 venv\Scripts\python.exe
- PowerShell에서 serial 실행
- pytest --basetemp를 지정하지 않는다.
- existing conftest가 만드는 trusted OS temp를 사용한다.
- workspace 내부 .tmp를 test data root로 사용하지 않는다.
- live user_data/knowledge/.runtime/sessions/workspace를 fixture로 쓰지 않는다.
- broad parallel pytest를 먼저 실행하지 않는다.
- targeted→adjacent→contract→compatibility 순으로 넓힌다.

Windows process가 0xc0000409 또는 output 없는 timeout을 반복하면:

1. 원 command, 시간, PID, exit를 기록한다.
2. 같은 loop를 무한 반복하지 않는다.
3. canonical Python direct invocation을 한 번만 재시도한다.
4. 다시 반복되면 host-dependent gate로 중단한다.
5. timeout을 pass 또는 code failure로 해석하지 않는다.

### 12.2 H1 focused lane

~~~powershell
venv\Scripts\python.exe -m pytest code_assist_v1\tests\test_migrate_notion_legacy_incarnations.py code_assist_v1\tests\test_migrate_notion_workspace_incarnation.py code_assist_v1\tests\test_notion_workspace_incarnation_attestation.py code_assist_v1\tests\test_notion_workspace_incarnation_orchestration.py code_assist_v1\tests\test_notion_workspace_incarnation_rebind.py code_assist_v1\tests\test_notion_workspace_offline_rehearsal.py -q
~~~

최신 patch 뒤 반드시 projection/healing도 포함한다.

~~~powershell
venv\Scripts\python.exe -m pytest code_assist_v1\tests\test_notion_workspace_projection.py code_assist_v1\tests\test_notion_workspace_healing_apply.py code_assist_v1\tests\test_notion_workspace_healing_store.py -q
~~~

### 12.3 Migration chain lane

~~~powershell
venv\Scripts\python.exe -m pytest code_assist_v1\tests\test_migrate_notion_workspace_schema.py code_assist_v1\tests\test_migrate_notion_workspace_outbox.py code_assist_v1\tests\test_migrate_notion_workspace_healing.py code_assist_v1\tests\test_migrate_notion_workspace_healing_apply.py code_assist_v1\tests\test_migrate_notion_workspace_quarantine.py code_assist_v1\tests\test_migrate_notion_workspace_incarnation.py code_assist_v1\tests\test_migrate_notion_workspace_navigation.py code_assist_v1\tests\test_migrate_notion_workspace_page_tree.py code_assist_v1\tests\test_migrate_notion_workspace_lifecycle_kernel.py code_assist_v1\tests\test_migrate_notion_workspace_lifecycle_finalize.py code_assist_v1\tests\test_migrate_notion_workspace_structural_effects.py code_assist_v1\tests\test_notion_workspace_migration_chain_rehearsal.py code_assist_v1\tests\test_migrate_notion_legacy_change_tracking.py code_assist_v1\tests\test_migrate_notion_legacy_incarnations.py code_assist_v1\tests\test_migrate_notion_legacy_change_consumer.py -q
~~~

### 12.3.1 H5b source-consumer contract lane

~~~powershell
venv\Scripts\python.exe -m pytest code_assist_v1\tests\test_migrate_notion_legacy_change_consumer.py code_assist_v1\tests\test_notion_legacy_change_consumer.py code_assist_v1\tests\test_notion_legacy_changes.py code_assist_v1\tests\test_notion_workspace_incarnation_orchestration.py code_assist_v1\tests\test_notion_workspace_healing_apply.py -q
~~~

### 12.3.2 H5e design-only lane

아래 lane은 destructive ack를 실행하지 않는다. 현재 v3 semantic hardening과 미승인 설계
contract가 fail-closed 상태인지 확인한다.

~~~powershell
venv\Scripts\python.exe -m pytest code_assist_v1\tests\test_notion_legacy_change_consumer.py code_assist_v1\tests\test_notion_legacy_change_consumer_orchestration.py code_assist_v1\tests\test_verify_notion_h5e_ack_contract.py -q
~~~

verifier만 직접 실행할 수도 있다.

~~~powershell
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_h5e_ack_contract
~~~

출력이 `valid=true`, `status=proposed_not_authorized`, `implemented=false`,
`destructive_ack_enabled=false`가 아니면 H5e 이후 작업을 중단한다.

### 12.3.3 NP2B-1/2/3 navigation backend, route and default-off UI lane

~~~powershell
venv\Scripts\python.exe -m pytest code_assist_v1\tests\test_verify_notion_np2b_navigation_contract.py -q
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_np2b_navigation_contract
node code_assist_v1\tests\test_notion_navigation_ui.js
node code_assist_v1\tests\test_notion_ui_runtime.js
node code_assist_v1\tests\test_auth_csrf_frontend.js
~~~

`valid=true`, `status=ui_contract_tested_default_off`,
`backend_schema_implemented=true`, `backend_store_api_implemented=true`,
`routes_enabled=true`, `ui_enabled=false`, `recents_write_enabled=false`가 모두 필요하다.

### 12.3.4 NP2C current 5-designed/4e-audited/1317-runtime regression lane

~~~powershell
venv\Scripts\python.exe -m pytest code_assist_v1\tests\test_migrate_notion_workspace_lifecycle_kernel.py code_assist_v1\tests\test_migrate_notion_workspace_lifecycle_finalize.py code_assist_v1\tests\test_migrate_notion_workspace_structural_effects.py code_assist_v1\tests\test_migrate_notion_workspace_structural_finalize.py code_assist_v1\tests\test_migrate_notion_workspace_lifecycle_subtree.py code_assist_v1\tests\test_migrate_notion_workspace_lifecycle_subtree_finalize.py code_assist_v1\tests\test_notion_workspace_page_lifecycle_kernel.py code_assist_v1\tests\test_notion_workspace_page_lifecycle_finalize.py code_assist_v1\tests\test_notion_workspace_page_lifecycle_structural_finalize.py code_assist_v1\tests\test_notion_workspace_page_lifecycle_subtree_kernel.py code_assist_v1\tests\test_notion_workspace_page_lifecycle_subtree_finalize.py code_assist_v1\tests\test_notion_workspace_page_lifecycle_subtree_orchestration.py code_assist_v1\tests\test_notion_legacy_page_lifecycle_subtree_bounds.py code_assist_v1\tests\test_notion_workspace_page_tree.py code_assist_v1\tests\test_notion_legacy_page_lifecycle.py code_assist_v1\tests\test_notion_legacy_page_structure.py code_assist_v1\tests\test_notion_legacy_page_structural.py code_assist_v1\tests\test_notion_legacy_page_duplicate_trash_restore.py code_assist_v1\tests\test_notion_workspace_page_structural_rehearsal.py code_assist_v1\tests\test_verify_notion_np2c_page_tree_contract.py code_assist_v1\tests\test_verify_notion_np2c_archive_characterization.py code_assist_v1\tests\test_verify_notion_np2c_archive_evidence_bundle.py -q
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_np2c_page_tree_contract
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_np2c_archive_characterization
~~~

Expected machine result is `valid=true`, contract version 19, status
`official_archive_source_contract_designed_source_v5_and_workspace_1318_blocked_default_off`, current schema 1317,
candidate schema 1318, 4a design, 4b source adapter, 4c schema, 4d finalizer/rehearsal, 4e independent audit and 5 Archive design true, Archive source/1318/runtime commands/permanent delete false. Before NP2C-5a or later work, record the exact hashes of the 4b source contract/adapter/tests,
structural source adapter/orchestration, store, 1313→1317 migrations, 1317 finalizer/orchestration, machine contract and all
focused tests. If any owned input changes after that snapshot, stop and re-audit instead of
merging a cached patch.

The separate Archive characterization verifier must also report `valid=true`, status `prepared_not_authorized_not_executed`, scenario/profile counts `16/11`, and both live-execution and source/catalog-implementation authorization false. The evidence-bundle adversarial tests must pass, but the acceptance CLI itself is run successfully only after a real approved external bundle exists. Neither preparation result is live evidence.

### 12.4 Adjacent NP-2A lane

포함:

- workspace store/P1
- locator projection
- outbox/delivery
- reconciliation/security
- pages/routes/auth isolation
- offline rehearsal
- legacy changes

정확한 selection은 수정 파일 dependency를 보고 선언한다. 전체 디렉터리를 무작정 병렬 실행하지 않는다.

### 12.5 Machine contract lane

포함:

- parity manifest verifier/tests
- parity catalog verifier/tests
- route contract verifier/tests
- route semantics verifier/tests
- workspace route contract verifier/tests
- evidence policy
- differential core

현재 알려진 구조 기준:

- main route 59
- operation semantics 61
- hard operations 15
- workspace overlay route 9
- workspace overlay operation 15
- workspace overlay hard operation 15

### 12.6 Compatibility lane

포함:

- atomic save compatibility/migration
- Notion routes
- publication adapter
- auth isolation
- publication UI
- editor/UI runtime JS
- Second Brain/Knowledge regression

Knowledge regression을 실행하기 전 fixture isolation과 protected path를 다시 확인한다.

## 13. Evidence와 checkpoint

각 slice 시작과 종료에 고유 checkpoint를 남긴다. shared status 파일 소유권이 없으면 새 고유 파일이나 외부 실행 기록에 남기고 STATUS_DELTA를 owner에게 전달한다.

권장 checkpoint 구조:

~~~json
{
  "run_id": "unique-id",
  "model": "gpt-5.6-sol",
  "reasoning_effort": "xhigh",
  "observed_goal": {
    "tool_status": "paused|active|null",
    "document_status": "summary"
  },
  "slice": "H1-proof-actor-readiness-replay",
  "status": "planned|running|tested|blocked",
  "owned_files": [],
  "protected_files": [],
  "input_hashes": {},
  "output_hashes": {},
  "last_successful_command": "",
  "exit_code": 0,
  "tests": {
    "passed": 0,
    "failed": 0,
    "skipped": 0
  },
  "live_mutation": false,
  "feature_flags_changed": false,
  "next_exact_command": "",
  "stop_reason": null
}
~~~

명령 증거에는 다음을 기록한다.

- cwd
- interpreter
- exact arguments
- start/end time
- exit code
- pass/fail/skip
- input/output source hashes
- environment/host failure

live characterization evidence는 EVIDENCE_POLICY를 따른다. raw DOM, cookies, network payload, screenshot, token, 실제 사용자 콘텐츠는 repo에 commit하지 않는다.

## 14. Feature flag·canary·kill switch

- 새 runtime path의 safe default는 off다.
- readiness가 false면 flag를 켤 수 없다.
- flag enable은 code merge와 별도 승인 사건이다.
- resolver, consumer, destructive ack, UI를 하나의 flag로 동시에 켜지 않는다.
- shadow telemetry→read-only canary→mutation canary 순서다.
- kill switch는 legacy 안전 경로와 data consistency 상태를 명시해야 한다.
- resolver cutover 뒤 directory fallback을 되살리는 kill switch는 보안상 허용되지 않을 수 있으므로 사전에 rollback 전략을 설계한다.
- canary는 합성 workspace에서 시작한다.

## 15. 문서 drift 원장

닫힌 drift:

1. MASTER_PLAN/ROADMAP/NP2A의 1308→1309 동기화
2. source incarnation nonce, attestation/rebind, offline rehearsal의 현재 상태 반영
3. H5a~H5d 비파괴 consumer foundation 반영

계속 추적할 drift:

1. `NOTION_PARITY_SPEC.md`의 오래된 완료 표현과 test count
2. manifest의 `exists` 의미와 일부 notes
3. catalog가 아직 NP-2/NP-3 일부 scenario만 포함한다는 사실
4. NP-8/NP-9/NP-11의 moving/separate/excluded dependency 표현
5. route count와 semantics rule count 용어 혼용
6. H5e는 설계-only이며 source v4/ack가 구현됐다고 표현하면 안 됨
7. latest current-tree test evidence와 checkpoint hash가 달라질 때 문서 숫자를 자동 승격하지 않음

## 16. 위험·결정 대기 목록

### Release-blocking

- unattested active policy
- H5e conditional ack 설계 review와 명시적 승인
- source v4 no-ack schema/migration/fault/restore contract
- explicit offline conditional ack의 crash/restore evidence
- continuous scheduling의 별도 lifecycle/operations contract
- resolver zero-leak/fail-closed
- directory-scan fallback 제거 전 readiness/cutover gate

### 운영 승인 필요

- live migration
- destructive watermark ack
- continuous consumer
- resolver cutover
- feature flag enable
- canary 사용자/workspace
- live reference capture

### 별도 제품/법무 결정

- native/offline 범위
- provider connectors
- Enterprise tenant
- AI/Agents 범위
- Notion 브랜드/표현
- 공식 API integration

## 17. xhigh가 넘겨받은 첫 턴의 정확한 순서

1. AGENTS.md를 완전히 읽는다.
2. get_goal을 확인하고 결과를 기록한다.
3. 이 문서와 ROADMAP, MASTER_PLAN, EVIDENCE_POLICY, NP2A 계약, H5e conditional ack 설계를 읽는다.
4. Shared Knowledge 작업과 Notion 작업의 경계를 다시 확인한다.
5. git metadata가 정상인지 read-only 확인한다. 비정상이면 init하지 않는다.
6. H5a~H5e checkpoint와 핵심 source/doc hash/mtime을 다시 계산한다.
7. drift가 있으면 구현하지 않고 diff-free source audit를 한다.
8. source v3/H5e-0 및 NP2C machine contract v19/current runtime 1317 focused baseline을 serial 실행한다.
9. H5e machine contract가 `proposed_not_authorized`, `implemented=false`, `destructive_ack_enabled=false`인지 확인한다. NP2C contract는 status `official_archive_source_contract_designed_source_v5_and_workspace_1318_blocked_default_off`, current schema 1317, candidate schema 1318, 4a design·4b source adapter·4c schema·4d finalizer/rehearsal·4e independent audit·5 Archive design true, Archive source/1318/runtime/permanent-delete false인지 확인한다.
10. NP2C-5a matrix verifier가 `prepared_not_authorized_not_executed`, scenario/profile 16/11, live/implementation authorization false인지 확인하고 authorization/evidence-bundle adversarial tests도 실행한다. 실제 browser는 external approval record가 current-time preflight에서 true이고 isolated tenants/evidence root가 있을 때만 연다. 그 전에는 completion verifier 성공을 주장하지 않는다.
11. NP2B machine contract가 backend schema/store API/routes/UI implementation true, runtime UI/recents=false인지 확인한다.
12. 사용자/운영자의 destructive ack 승인이 없으면 source v4 또는 ack code를 시작하지 않는다.
13. NP2B-1의 1310 hashes, migration/store tests와 checkpoint를 먼저 재검증한다.
14. H5e가 승인된 경우에도 H5e-1 no-ack schema/migrator만 구현하고 fault/restore review에서 중단한다.
15. NP2C-3e1 source adapters, NP2C-3e2 frozen 1314 guards, NP2C-3e3 frozen 1315 finalizer/rehearsal, NP2C-4a design, NP2C-4b source adapter/file recovery, NP2C-4c frozen 1316 guards, NP2C-4d frozen 1317 finalizer/rehearsal, NP2C-4e recovery/audit와 NP2C-5 Archive design 문서·machine contract의 current hashes를 재검증한다.
16. 3e1/3e2/3e3/4a/4b/4c/4d/4e/5와 5a preparation/authorization/acceptance harness를 재구현하지 않는다. 다음 cohesive slice는 current-time preflight를 통과한 `NP2C-5a-approved-business-enterprise-live-characterization`이며 승인된 fixture/evidence boundary가 없으면 멈춘다.
17. 4e checkpoint가 source-ledger-first retry, frozen 1316/1317 independence, three-phase rollback, 11,004/11,005 bound, redaction와 deep-health를 실제 current hashes에 결속하고, 5 checkpoint가 source v4→candidate v5/workspace 1318 lineage와 구현 금지를 결속하는지 재확인한다.
18. 4a~5 checkpoint는 완료 상태로 보존한다. Continuous worker, resolver, runtime UI, official Archive/unarchive 구현, public duplicate, destructive ack와 purge는 별도 승인/gate 전까지 시작하지 않는다.

현재 완료를 고정해야 하는 slice 이름:

NP2C-5-official-archive-source-contract-design
NP2C-5a-evidence-acceptance-harness
NP2C-5a-live-authorization-preflight

## 18. 절대 금지 작업

- live user_data나 real notion.db를 열거나 수정
- 운영 migration apply
- request/startup automatic migration
- destructive watermark ack 조기 활성화
- continuous consumer 조기 등록
- resolver/UI cutover 조기 연결
- app_code.py 또는 Second Brain 경계에 우회 등록
- Shared Knowledge schema/status/traceability의 무단 수정
- Git init
- 다른 개발자의 변경 revert/cleanup
- frozen version 아래에서 DDL을 조용히 변경
- secret/path/token/content를 evidence에 기록
- 테스트 통과만으로 live/differential parity 선언
- timeout/abort를 pass로 기록

## 19. 최종 인수 체크리스트

### 1309 correctness

- [x] proof actor와 bound_by 분리
- [x] ordinary/rebound actor derivation
- [x] readiness exact validator
- [x] direct attest replay exact validator
- [x] direct rebind replay exact validator
- [x] tamper tests
- [x] latest focused green
- [x] adjacent green
- [x] migration chain green
- [x] deep health/readiness semantics 고정

### NP-2A operational readiness

- [x] full rebind offline wrapper
- [x] exact sentinel/quiescence
- [x] restored snapshot rehearsal
- [x] source-fenced effect receipt와 checkpoint-loss replay foundation
- [x] source-local durable pass/work/ledger와 bounded non-destructive materialization
- [x] offline effect/completion/no-ack restart rehearsal
- [x] superseded pending semantics와 H5e design contract
- [ ] approved source v4 no-ack schema/migrator
- [ ] generation-conditional ack
- [ ] continuous scheduler/worker
- [ ] resolver zero-leak
- [ ] directory scan 0
- [ ] file/public cutover
- [ ] feature flag/kill switch
- [ ] canary/restore evidence

### NP-2B navigation

- [x] official-reference/unknown 분리
- [x] candidate 1310 sidebar/favorites design contract
- [x] zero-source-I/O/visibility/replay/ledger/fault/privacy gate
- [x] 1309→1310 migration
- [x] navigation store API
- [x] protected route overlay
- [x] default-off UI/a11y/stale response fencing
- [ ] Recents retention/coalescing 결정과 implementation
- [ ] tree command/drag/keyboard/focus

### NP-2C page tree and lifecycle

- [x] 1311 page-tree/saga foundation
- [x] 1312 lifecycle lease/fence/namespace guards
- [x] offline initial materializer
- [x] disabled rename protocol kernel
- [x] rename source request/receipt and 1313 catalog-finalize design
- [x] v3-ledger-backed temporary-shard rename source adapter
- [x] frozen 1312→1313 lifecycle catalog guard/finalizer
- [x] sentinel-bound end-to-end rename response-loss rehearsal
- [x] create/move/reorder design/machine contract와 candidate 1314 필요성
- [x] create/move/reorder legacy-v3 source adapters
- [x] frozen 1313→1314 schema/guards
- [x] create/move/reorder atomic catalog finalizers/rehearsal
- [x] duplicate/Trash/restore design과 machine contract v14
- [x] duplicate/Trash/restore source-v3 adapters/file recovery와 machine contract v15
- [x] frozen 1315→1316 lifecycle-subtree schema/guards와 machine contract v16
- [x] duplicate/Trash/restore atomic catalog finalizers/rehearsal
- [x] duplicate/Trash/restore independent recovery/bounds/redaction/deep-health audit
- [x] official Archive/Trash semantic separation and source-lineage design
- [x] machine contract v19 and adversarial verifier
- [x] source v4 reservation → candidate v5 and workspace 1318 non-implementation gate
- [x] NP2C-5a 16-scenario/11-profile preparation matrix, verifier and runbook
- [ ] approved Business/Enterprise live-characterization matrix
- [ ] official Archive/unarchive source lifecycle
- [ ] protected routes/default-off UI
- [ ] retention/purge approval

### 전체 parity

- [ ] NP-0 complete
- [ ] NP-1 complete
- [ ] NP-2A~E complete
- [ ] NP-3 complete
- [ ] NP-4 complete
- [ ] NP-5 complete
- [ ] NP-6 complete
- [ ] NP-7 Core Web complete
- [ ] 채택된 NP-8/9/10 complete
- [ ] NP-11 complete
- [ ] live-characterized 필수 scenario 100%
- [ ] differential-passed 필수 scenario 100%
- [ ] Second Brain hard gate
- [ ] security/a11y/performance/legal/operations approval

## 20. 인계 요약

현재 작업은 “전체 parity의 중반”이 아니다. NP-1 기반과 NP-2A의 깊은 backend safety substrate가 상당히 진행된 상태다. NP-2A 자체도 아직 runtime cutover와 운영 gate가 남아 약 80%로 본다. NP-2B~NP-2E와 NP-3~NP-11의 최종 gate는 대부분 남아 있으므로 전체 engineering progress는 약 10%로만 본다.

xhigh의 다음 목표는 기능을 무작정 넓히는 것이 아니다. H1~H5e, NP2B-1/2/3, NP2C-2b와 NP2C-3a/3b/3c/3d/3e1/3e2/3e3/4a/4b/4c/4d/4e의
current-tree evidence, frozen hashes와 machine contract의 금지 상태를 먼저 재확인한다. NP2C-3d exact-sentinel
rename rehearsal, NP2C-3e1 source adapters, NP2C-3e2 frozen 1314 guards, NP2C-3e3 frozen 1315 finalizer/rehearsal와
NP2C-4a duplicate/Trash/restore design, NP2C-4b source adapter/file recovery, NP2C-4c frozen 1316 guards, NP2C-4d frozen 1317 atomic finalizer/exact-sentinel rehearsal, NP2C-4e source-replay/adversarial audit과 NP2C-5 official Archive reference design은 완료·checkpoint 대상이므로 다시 만들지 않는다.
NP2C-5a의 16-scenario/11-profile matrix, strict verifier와 runbook은 준비됐지만 live execution authorization과 evidence는 0이다. 다음 cohesive slice는 승인된 `NP2C-5a-approved-business-enterprise-live-characterization`이다. Source v4/v5와 workspace 1318은 아직 구현되지 않았고, characterization 없이 Archive DDL·adapter·finalizer를 시작하거나 route/UI/runtime에 연결하지 않는다.
Destructive ack가 별도로 승인되더라도 H5e source v4 no-ack schema/migrator부터 분리 구현하며 offline ack API,
continuous scheduler, resolver와 UI flag enable은 각각 별도 gate다.

이 순서를 지키면 ultra 단계에서 축적한 안전성 계약을 잃지 않고 xhigh가 더 빠르게 실행할 수 있다.
