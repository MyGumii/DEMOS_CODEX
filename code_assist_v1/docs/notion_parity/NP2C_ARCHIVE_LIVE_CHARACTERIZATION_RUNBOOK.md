# NP2C-5a official Archive live-characterization runbook

상태: authorization-inspection/preflight/execution/acceptance harness 준비 완료, 실행 미승인, live observation 0건

기준일: 2026-08-14 Asia/Seoul

Machine matrix: `docs/notion_parity/np2c_archive_live_characterization_matrix.json` version 1

> **Optional paid Archive runbook:** 이 승인·Business/Enterprise 요구사항은 official Archive를 실제 채택해
> 연구할 때만 적용한다. Core Free Web UI 개발에는 적용되지 않으며, 현재 Core reference 절차는
> `docs/notion_parity/CORE_FREE_WEB_UI_CHARACTERIZATION_RUNBOOK.md`를 따른다. Notion plan transition은
> local 제품의 billing/entitlement 복제 요구가 아니다.

이 문서는 NP2C-5의 reference-only 설계를 승인된 실제 제품 관찰로 바꾸는 절차다. 이 문서 자체는 Notion 계정 접근, live execution, source v5, workspace 1318, route/UI/runtime, migration, destructive acknowledgement 또는 purge를 승인하지 않는다.

## 1. 현재 gate

현재 상태는 정확히 다음과 같다.

- NP2C page-tree machine contract는 v19다.
- workspace runtime은 frozen `2026081317`이다.
- H5e source v4 `2026081304`, Archive source v5 `2026081305`, workspace `2026081318`은 모두 미구현이다.
- characterization matrix는 `prepared_not_authorized_not_executed`다.
- 외부 authorization preflight와 evidence-bundle acceptance verifier 및 adversarial tests는 준비됐지만 승인 record와 검증할 bundle은 없다.
- live-characterized/differential-passed/NP2C-5a-complete 주장은 모두 false다.
- 다음 단계는 승인과 격리 환경 확보이며 DDL 또는 runtime 구현이 아니다.

## 2. 실행 전 필수 승인

아래 항목이 모두 문서화되지 않으면 live UI를 열지 않는다.

1. 사용자의 명시적 live-characterization 승인;
2. Business 또는 Enterprise 합성 테스트 tenant;
3. 실제 조직·개인 콘텐츠와 분리된 합성 fixture workspace;
4. 승인된 operator와 독립 reviewer;
5. plan, role, platform, locale별 실행 범위;
6. repository 밖의 암호화된 redacted evidence root와 보존 기간;
7. raw capture 삭제 기한과 실행 후 fixture cleanup 계획;
8. public-link 및 plan-transition 시나리오의 별도 위험 승인;
9. 정상 공개 UI만 사용하고 API/private endpoint/network payload/scraping을 사용하지 않는다는 확인.

승인은 operation permission이지 제품 의미의 증거가 아니다. 승인 시점에도 모든 미관찰 항목은 `unknown`으로 유지한다.

승인 record는 repository 밖의 승인 evidence root 아래 redacted JSON으로 둔다. Contract는 `np2c5a_archive_live_authorization_v1`이고 current matrix raw SHA-256, 위 승인 항목, 그리고 characterization session만 true인 exact claims를 포함한다. 유효 창은 최대 24시간이며 승인 시각도 시작 시각보다 24시간 이상 앞설 수 없다. Record에는 실제 tenant, user, workspace 이름·ID, 이메일, 절대경로 또는 token을 쓰지 않고 allowlisted synthetic identifier만 쓴다. Record 작성자나 에이전트가 스스로 `approved=true`를 써서 승인으로 만들 수 없도록, 승인자는 exact `authorization` 객체의 canonical SHA-256을 별도 채널로 전달한다. 에이전트가 계산한 값을 스스로 승인 digest로 재사용하면 안 된다.

승인 요청 전에 다음 inspection을 실행해 exact `authorization` 객체의 canonical digest 후보를 확인한다.

~~~powershell
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_np2c_archive_authorization <external-authorization.json> --evidence-root <absolute-external-redacted-root> --inspect-only
~~~

Inspection 성공은 `inspection_valid=true`와 `status="inspection_only_not_authorized"`만 뜻한다. 출력은 항상 `valid=false`, `authorized_to_begin_characterization=false`, `approval_digest_matched=false`, `implementation_authorized=false`, `completion_claimed=false`여야 한다. Operator는 전체 redacted record와 출력의 `authorization_sha256`을 승인자에게 전달하고, 승인자는 그 exact digest를 repository·record 작성 경로와 분리된 채널에서 명시적으로 승인해야 한다. Inspection 출력 자체, 에이전트의 계산 행위 또는 record 안의 `approved=true`를 승인으로 취급하지 않는다.

브라우저를 열기 직전에 승인자가 제공한 exact digest로 다음 current-time preflight를 실행한다.

~~~powershell
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_np2c_archive_authorization <external-authorization.json> --evidence-root <absolute-external-redacted-root> --approved-authorization-sha256 <out-of-band-user-approved-sha256>
~~~

`status="authorized_preflight"`, `valid=true`, `authorized_to_begin_characterization=true`, `approval_digest_matched=true`, `approved_plans=["business","enterprise"]`, `normal_public_ui_only=true`, `implementation_authorized=false`, `completion_claimed=false`가 모두 확인되지 않으면 실행하지 않는다. 출력의 authorization record SHA-256과 승인자가 제공한 authorization SHA-256만 identifier-free session checkpoint에 기록한다. Inspection 이후 record의 한 field라도 바뀌면 digest가 달라지므로 기존 승인은 무효다. 중단 뒤 재개하거나 승인 창이 지났으면 새 record를 inspection하고 exact digest를 다시 승인받은 뒤 preflight를 통과해야 한다.

## 3. xhigh 시작·재개 절차

매 실행 세션은 다음 순서를 지킨다.

1. `code_assist_v1/AGENTS.md`를 완전히 다시 읽는다.
2. active goal을 조회하고 교체하거나 완료 처리하지 않는다.
3. NP2C-5 checkpoint, machine contract v19, matrix v1, 이 runbook과 evidence policy를 다시 읽는다.
4. 각 파일의 SHA-256과 mtime을 재계산한다.
5. `.git` metadata를 read-only 확인한다. 비정상이면 `git init`이나 commit 주장을 하지 않는다.
6. 아래 verifier를 serial 실행한다.

~~~powershell
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_np2c_page_tree_contract
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_np2c_archive_characterization
venv\Scripts\python.exe -m pytest code_assist_v1\tests\test_verify_notion_np2c_page_tree_contract.py code_assist_v1\tests\test_verify_notion_np2c_archive_characterization.py code_assist_v1\tests\test_verify_notion_np2c_archive_authorization.py code_assist_v1\tests\test_verify_notion_np2c_archive_evidence_bundle.py -q
~~~

7. 승인 record를 먼저 inspection하고 사용자가 exact candidate digest를 별도 채널에서 승인한다. Fixture manifest, operator/reviewer, 외부 evidence root와 cleanup approval이 하나라도 없거나 승인 digest를 사용한 authorization preflight가 현재 시각에 통과하지 않으면 실행을 중단한다.
8. 승인 뒤에도 한 번에 하나의 scenario/profile run만 수행하고 매 run 뒤 checkpoint를 남긴다. 각 세션 재개 시 preflight를 다시 실행한다.

## 4. 허용되는 관찰 방법

허용:

- 사용자가 정상적으로 로그인한 공개 제품 UI;
- 화면에 보이는 state, banner, menu/action availability, search/sidebar/database 결과;
- 승인된 accessibility tree, keyboard/focus 결과와 crop된 screenshot;
- 합성 fixture에서의 정상 사용자 action;
- app/browser/OS의 exact version과 plan/role/platform/locale 기록.

금지:

- cookie, localStorage, password, session store 또는 token 열람;
- private endpoint, undocumented API, network response body 또는 webhook payload 수집;
- public API를 복제·mutation oracle로 사용;
- DOM 대량 수집, scraping, CAPTCHA/rate-limit/security control 우회;
- 실제 사용자·조직 workspace 또는 운영 `user_data`, `.runtime`, `knowledge`, `sessions`, workspace DB 접근;
- source/catalog schema 또는 local runtime을 characterization 중 변경;
- raw screenshot, DOM, accessibility snapshot, video 또는 사용자 콘텐츠를 repository에 저장.

## 5. Fixture 규칙

각 fixture는 matrix의 `FX-*` placeholder를 하나의 실행 내 synthetic identity로만 구현한다.

- 실제 page/workspace/user ID는 run별 salt를 사용한 placeholder로 바꾼다.
- fixture manifest에는 content가 아니라 topology, role, state와 redacted hash만 기록한다.
- page body, title, comment, file name과 public link는 합성 값만 사용한다.
- public scenario는 별도 승인된 synthetic page만 사용한다.
- plan-loss scenario는 tenant owner가 별도 승인한 reversible transition이 없으면 실행하지 않고 `unknown`으로 유지한다.
- concurrency scenario는 두 승인된 정상 UI session만 사용한다. 네트워크 fault injection이나 private traffic manipulation은 금지한다.

## 6. 한 scenario/profile run의 정확한 절차

1. scenario ID, profile ID, fixture manifest SHA-256과 reference source를 고정한다.
2. action 전 visible state를 redacted canonical record로 작성한다.
3. matrix에 정의된 action을 정상 UI에서 순서대로 한 번 실행한다.
4. matrix의 모든 `observation_dimensions`를 각각 `observed`, `not_available`, `ambiguous`, `reference_conflict` 중 하나로 기록한다.
5. 결과가 문서와 다르면 문서를 따르거나 UI를 따르지 말고 `reference-conflict`로 남긴다.
6. 관찰하지 못한 dimension은 절대로 다른 profile/platform 결과로 보간하지 않는다.
7. input/result를 canonical normalization하고 redaction 검증 뒤 SHA-256을 계산한다.
8. `EVIDENCE_POLICY.md`의 필수 필드를 가진 manifest를 외부 승인 경로에 작성한다.
9. 다음 명령으로 redacted manifest를 검증한다.

~~~powershell
venv\Scripts\python.exe -m code_assist_v1.tools.notion_parity.evidence <external-redacted-manifest.json> --compact
~~~

10. 독립 reviewer가 action, observation, redaction, reference conflict와 hash를 확인한다.
11. raw capture를 승인된 기한 내 삭제하고 deletion record만 남긴다.
12. aggregate checkpoint에는 identifier, path, nonce, digest 원문, token, content를 넣지 않는다.

## 7. 시나리오 coverage

Matrix v1은 다음 16개 scenario를 모두 요구한다.

1. active descendant가 있는 parent archive;
2. independently archived와 inherited archived descendant가 섞인 nested unarchive;
3. archived boundary에서 create/move in/move out;
4. archived root/child duplicate;
5. archived→Trash;
6. formerly archived page의 Trash restore;
7. archived content edit/comment/mention/notification;
8. default sidebar/Search와 explicit Archive-aware view;
9. database row/view/data-source behavior;
10. backlink/relation/rollup/mention/synced-block behavior;
11. owner/member/guest/admin permission matrix;
12. approved plan-loss transition;
13. public/published-link behavior;
14. web/Windows/macOS/iOS/Android variance;
15. concurrent subtree/permission change와 reload convergence;
16. existing direct workspace link와 IAM.

Scenario를 삭제하거나 하나의 결과로 여러 platform/role을 채우면 matrix verifier가 실패해야 한다.

## 8. Evidence 판정

개별 run은 reviewer가 승인한 manifest가 있어야 `live-characterized`가 될 수 있다. 자동 test, reference 문서, screenshot 하나 또는 UI가 존재한다는 사실만으로는 승격하지 않는다.

NP2C-5a 전체 gate는 다음이 모두 참일 때만 닫힌다.

- 16개 required scenario에 reviewed live evidence가 있다.
- required profile은 관찰됐거나 실행 불가능한 이유가 명시적으로 `unknown`으로 기록됐다.
- 모든 observation dimension이 관찰 또는 명시적 unknown/conflict로 설명된다.
- public behavior, nested unarchive, plan loss와 permission denial을 추정하지 않았다.
- reference conflict가 숨겨지지 않았다.
- raw evidence가 repository에 없고 redaction verifier가 통과했다.
- 독립 semantic decision record가 승인됐다.
- Second Brain hard gate와 보호 경계가 변하지 않았다.

Profile 일부가 unavailable이면 matrix coverage는 설명할 수 있지만 NP2C-5a 완료를 자동 허용하지 않는다. 필수 시나리오 자체에 reviewed live evidence가 없으면 gate는 계속 open이다.

### 8.1 완료용 외부 evidence bundle

개별 run의 manifest 검증과 전체 NP2C-5a 완료 판정은 분리한다. Matrix v1은 16개 scenario와 11개 profile의 allowlisted 조합으로 정확히 46개 run을 요구한다. 진행 중 checkpoint는 `unknown` 또는 `reference-conflict`를 정직하게 보존할 수 있지만, `reviewed_complete` bundle은 46개 pair를 matrix 순서대로 관찰하고 unknown/conflict를 모두 독립 review로 해소한 경우에만 통과한다. 해소되지 않았다면 bundle을 완료 상태로 만들지 않고 gate를 open으로 둔다.

완료 bundle은 다음 fail-closed 계약을 따른다.

- bundle과 모든 artifact는 명시적으로 승인된 absolute external evidence root 아래에 있고 repository, symlink, reparse point 또는 hard link를 사용하지 않는다.
- verifier는 directory를 scan하지 않고 bundle에 명시된 파일만 연다. Bundle 4 MiB, manifest/action/fixture 각 1 MiB, artifact 각 16 MiB, 전체 512 MiB, run 128개가 상한이다.
- 각 run은 승인 operator, tenant, scenario/profile, canonical evidence manifest, matrix와 exact 일치하는 synthetic fixture/action script, 정확히 하나의 result, 정확히 하나의 redaction report를 결속한다. Result는 matrix의 모든 observation dimension을 순서대로 `observed`와 redacted observation hash로 기록해야 한다. 모든 명시 경로는 run 간에도 고유해야 한다.
- fixture/action/result/redaction bytes의 SHA-256을 실제 파일에서 다시 계산한다. Manifest `input_sha256`은 action script에, `result_sha256`은 result artifact에 exact 결속된다.
- screenshot 이외의 supporting/evidence file은 redacted JSON이어야 한다. Redaction report는 모든 다른 artifact hash, reviewer와 review time을 exact 결속해야 하므로 screenshot도 이 report 없이는 허용되지 않는다.
- Business와 Enterprise synthetic tenant, 독립 operator/reviewer, 승인 시간창, public-link/plan-transition 별도 승인과 normal-public-UI-only 경계를 모두 검증한다.
- semantic decision record는 별도 명시 파일로 모든 16개 scenario decision을 순서대로 담고 그 실제 bytes의 SHA-256, reviewer/time/next gate와 정확히 일치해야 하며 구현 권한은 계속 false다. Acceptance는 `live_characterized`와 `np2c5a_complete`만 승인하며 differential, source v5, workspace 1318, runtime, NP2C 전체 또는 overall parity는 승인하지 않는다.

승인 bundle이 생긴 뒤 repository root에서 다음 명령을 실행한다. `<bundle.json>`도 external root 아래의 명시 파일이어야 한다.

~~~powershell
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_np2c_archive_evidence_bundle <bundle.json> --evidence-root <absolute-external-redacted-root> --approved-authorization-sha256 <out-of-band-user-approved-sha256>
~~~

성공 결과는 `valid=true`, `accepted=true`, `approval_digest_matched=true`, exact expected run count, `reference_conflict_count=0`, `implementation_authorized=false`여야 한다. Preflight와 completion은 동일한 out-of-band 승인 digest를 사용해야 한다. 현재는 승인 bundle과 live evidence가 없으므로 이 명령의 성공 결과를 주장하지 않는다.

## 9. Characterization 뒤 산출물

승인된 관찰이 끝나면 source/schema code보다 먼저 다음을 작성한다.

1. scenario/profile별 redacted evidence manifest와 hash inventory;
2. reference conflict ledger;
3. nested Archive provenance/cohort decision;
4. create/move/duplicate/Trash/restore interaction decision;
5. plan/role/platform/public visibility decision;
6. exact source receipt와 catalog canonical-state 요구사항;
7. 구현이 허용되는 것과 여전히 unknown/blocked인 것;
8. independent reviewer sign-off와 NP2C-5a checkpoint.

그 뒤에만 machine contract를 reviewed result로 갱신하고 NP2C-5b source-v5 제안을 시작할 수 있다. NP2C-5a가 끝나도 H5e source v4는 별도 승인 없이 구현할 수 없으며 workspace 1318, route/UI, resolver와 runtime은 각각 후속 gate다.

## 10. 즉시 중단 조건

- 승인, tenant, reviewer, evidence root 또는 cleanup plan 누락;
- authorization preflight 실패, 만료 또는 matrix hash drift;
- Business/Enterprise plan 또는 requested role 부재;
- real workspace/user content 노출;
- private API/network/cookie/token 접근 필요;
- public-link 또는 plan transition의 별도 승인 누락;
- matrix/checkpoint/source hash drift;
- 관찰되지 않은 의미를 구현 결정으로 바꿔야 하는 상황;
- source v4/v5, workspace 1318, route/UI/runtime 또는 migration 변경 필요;
- 보호 파일이나 다른 개발자의 owned file 변경 필요;
- redaction 실패, reference conflict 또는 reviewer 불일치.

중단은 실패를 숨기는 이유가 아니다. checkpoint에 stop reason과 마지막 검증된 hash/command를 기록하고 해당 dimension을 `unknown`으로 유지한다.
