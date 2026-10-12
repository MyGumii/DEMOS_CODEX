# Internal Collaborative Notion 메인 에이전트 실행 Handoff

상태: 새 로컬 메인 에이전트용 실행 인계 문서  
기록 시각: 2026-08-26T00:45:58+09:00  
작업공간: `G:\AI_Agent\DEMOS_CODEX`  
프로젝트: `G:\AI_Agent\DEMOS_CODEX\code_assist_v1`  
권장 실행 모델: `gpt-5.6-sol`, reasoning `xhigh`  
현재 실행 커서: `V0R-P1` 승인 완료, 외부 reference 실행 미시작  

이 문서는 새 메인 에이전트가 전체 목표와 현재 실행 커서를 한 번에
이어받기 위한 운영 handoff다. 기존 제품 계약, 패킷 카탈로그, 검증
래더, 체크포인트 또는 `AGENTS.md`를 대체하지 않는다. 충돌하면 현재
`AGENTS.md`와 해시가 검증된 기계 판독 계약이 우선한다.

## 1. 한 문장 목표

기존 Second Brain 계정으로 로그인하여 사용하는, 내부 조직용
Notion 유사 데스크톱 웹 애플리케이션을 완성한다. Notion의 요금제나
사업 로직을 복제하는 것이 아니라, 채택한 화면과 상호작용을 통제된
환경에서 픽셀 단위로 재현하고, 실시간 다중 사용자 협업과 Second
Brain 발행 경계, 백업·복구·권한 회수를 실제로 검증한 뒤 개발
`10111/10112/10113`에서 검증된 동일 릴리스 계보만 운영
`10011/10012`로 승격한다.

## 2. 사용자가 승인한 제품 계약

### 2.1 계정과 워크스페이스

- 별도 Notion 계정을 만들지 않는다. Agent/Second Brain의 활성 일반
  계정이 Notion의 사용자 주체가 된다.
- 개발에서는 `10111`의 승인된 개발 계정이 `10112`에서 같은 주체로
  보인다. 운영에서는 `10011` 로그인 주체가 `10012`에서 그대로
  사용된다.
- 각 사용자는 자신이 만든 워크스페이스를 소유하며 제품상 개수 제한
  없이 추가할 수 있다.
- 다른 사용자의 워크스페이스는 초대받은 경우에만 보인다. 페이지만
  공유받은 사용자는 해당 페이지와 허용된 하위 범위만 볼 수 있다.
- 사용자 기능 가용성은 모두 동일하지만, 워크스페이스/페이지 권한과
  SBE 개인·Part·Team·회사 권한은 서로 독립적이다. 기능이 있다는
  이유로 데이터 권한을 우회하지 않는다.
- 비활성·서비스·시스템 계정은 자동 프로비저닝 대상이 아니다.

### 2.2 필수 기능

- 워크스페이스 생성·전환·초대·역할 변경·탈퇴
- 페이지 트리 생성·이동·복제·즐겨찾기·Trash·복구
- 풍부한 블록 편집기, slash menu, drag, 키보드, undo/redo, 네이티브
  한글 IME
- 워크스페이스 및 페이지 공유, 상속된 full/edit/comment/view 권한,
  즉시 권한 회수와 비공개성
- 두 개 이상의 독립 브라우저 주체에서 presence, cursor, selection,
  동시 편집, 재접속 및 수렴
- 댓글·답글·reaction·mention·inbox
- 파일과 이미지, 페이지 히스토리·diff·revision restore
- table/board/list/gallery/calendar/timeline DB view
- filter/sort/group/relation/rollup/formula/template
- Markdown/CSV import/export, reminder
- 권한 인식 search/recent/favorite/backlink
- 정확한 커밋 Notion revision과 permission epoch에 묶인 Second Brain
  preview/approve/cancel/publication 및 attachment 경계
- 채택한 모든 가시 상태의 데스크톱 상호작용·픽셀 parity
- 조정된 백업, 새 root 복구, integrity 검사, rollback 및 canary

### 2.3 제외·연기 범위

- 요금제, billing, Free/Business/Enterprise 차등, 업그레이드 유도 UI
- public web publish와 public link
- 외부 이메일 guest
- Notion AI 복제
- 모바일 web parity와 native mobile app
- chart/dashboard/form/automation/feed/map 및 외부 calendar/mail 연동
- 전체 워크스페이스의 완전한 오프라인 탐색·검색·대량 동기화

리소스 안전 한도는 허용되지만 요금제 한도로 표현하면 안 된다.

### 2.4 동결된 비기능 기준

- 최소 부하 바닥: 등록 사용자 100명, 동시 연결 30개, 한 페이지 동시
  편집자 10명
- 지속 30 ops/s, burst 100 ops/s, attachment 동시 처리 10개
- 제품상 page/block/row 개수 제한 없음. 검증 fixture 바닥은
  workspace당 10,000 page, page당 5,000 block, DB당 100,000 row
- 디스크 안전선: 20 GiB 및 20% 중 하나라도 미달하면 신규 용량 소비
  write를 fail closed하되 기존 데이터는 숨기지 않는다.
- RPO 15분, RTO 60분
- backup 보관: 15분 세대 96개, 일간 30개, 월간 12개
- 사용자 page revision은 자동 삭제하지 않는다. Trash 기본 보관은
  30일이다.
- 오프라인 level 2: 이전에 연 페이지의 미전송 로컬 편집을 브라우저
  종료·재실행 후 복구한다. 재전송 전에 주체·워크스페이스·페이지·
  epoch를 다시 인증한다.
- 기준 화면: Chrome desktop, `ko-KR`, `Asia/Seoul`, light theme,
  `1440x900`과 `1280x800`, device scale 1, zoom 1.

정식 외부 출시를 위한 법무·브랜드 검토는 목표가 아니지만, 내부
보안·접근성·복구·감사·부하 검증은 생략하지 않는다.

## 3. 아키텍처 경계

- Notion collaboration canonical DB와 중앙 SBE SQLite는 서로 다른
  canonical authority와 schema lineage다.
- Notion의 membership, page access epoch, content, operation dedupe,
  event sequence, audit/outbox는 하나의 Notion transactional authority로
  처리한다.
- 모든 mutation은 service layer transaction을 통과하며 caller 제공
  `operation_id`와 request hash를 사용한다. audit와 outbox는 동일
  transaction에서 commit한다.
- legacy owner shard는 호환 read/projection일 수 있으나 새 경로와
  dual-write하거나 request-time directory scan의 권한 원본이 되면 안
  된다.
- real-time은 별도 gateway와 durable event log, one-time ticket,
  Origin/epoch binding, ack/replay/snapshot/compaction/backpressure를
  사용한다.
- editor는 Yjs 계열의 검증된 수렴 경로를 사용한다. whole-page
  last-writer-wins를 협업 완성으로 주장하지 않는다.
- revoke는 old epoch의 read/write/replay/search/inbox/cache/subscription을
  모두 fence한 뒤 완료된다.
- 이미 성공적으로 Second Brain에 발행된 item은 독립 SBE item이다.
  이후 Notion revoke/edit가 이를 자동 숨김·삭제·동기화하지 않는다.
- Notion page timestamp는 SBE canonical body/hash/FTS/dedup/semantic/
  rerank/excerpt에 들어가지 않는다.

## 4. 작업공간과 현재 런타임

| 구분 | 포트 | 역할 | 2026-08-26 00:45 KST 관측 |
|---|---:|---|---|
| 개발 Agent/계정 | 10111 | 로그인 및 계정 mutation authority | listening, `/healthz` 200 |
| 개발 Notion | 10112 | 개발용 협업 UI/API | listening, `/healthz` 200 |
| 개발 gateway | 10113 | realtime/compactor gateway | listening, `/health` 200, `READY` |
| 운영 Agent | 10011 | 운영 계정 authority | 현재 listen 미관측 |
| 운영 Notion | 10012 | 최종 승격 대상 | 현재 listen 미관측 |

- 개발 root:
  `G:\AI_Agent\DEMOS_CODEX\.dev\notion-collaboration-10111-10113`
- 현재 관측 launch id: `0b5939e460824ccaab2dc421b91bc5ba`
- 현재 포트 PID는 세션 재시작 때 달라질 수 있으므로 handoff의 PID를
  제어 식별자로 사용하지 않는다. root marker, launch descriptor,
  approval ID와 실제 listener를 다시 확인한다.
- 실행 중인 개발 서비스를 불필요하게 재시작하지 않는다.
- `10011/10012`, 개인 데이터, 운영 root는 현재 실행 범위가 아니다.
- 현재 폴더 트리는 Git repository로 인식되지 않았다. `git reset`,
  checkout 또는 Git 기반 정리를 전제로 하지 말고, 체크포인트의
  pre-edit hash와 정확한 파일 ownership으로 보존한다.

## 5. `AGENTS.md`와 다른 개발 흐름

`code_assist_v1/AGENTS.md`는 다른 Shared Knowledge Agent/SBE 개발에서도
사용하는 동적 전역 지침이다.

- 수정하거나 새 `AGENTS.md`/`agents.md`를 만들지 않는다.
- 새 패킷 시작 때마다 전체를 다시 읽고 SHA-256을 기록한다.
- 이 handoff 작성 시 hash:
  `452b14964e22cc02ac0bd5ad803d991ae30748ceaa7352191ace10f0fe03d996`
- hash가 바뀌면 변경된 지침을 다시 읽고 새 패킷에 bind한다. 과거
  receipt를 자동 폐기하거나 다른 개발자의 파일을 되돌리지 않는다.
- SBE seam, central schema, production 또는 backup target을 건드리기
  전에는 `AGENTS.md`의 18개 문서를 지정 순서대로 직접 읽고 현재
  `STATUS.md`, backlog, ownership snapshot을 다시 bind한다.
- Notion 패킷은 SBE hot contract, `STATUS.md`, traceability 또는
  `AGENTS.md`를 소유하지 않는다. 필요하면 명시적 ownership transfer를
  받고 별도 패킷으로 진행한다.
- `knowledge/`, `user_data/`, `.runtime/`, 기존 개인 Notion DB를 fixture나
  writable test target으로 사용하지 않는다.

## 6. 새 메인 에이전트의 필수 읽기 순서

다음 파일을 생략하지 말고 읽는다. hash drift가 있으면 실행을 멈추고
의미를 확인한다.

1. `code_assist_v1/AGENTS.md`
2. 이 handoff 문서
3. `docs/notion_parity/core_free_web_ui/internal_collaborative_product_decision.v1.json`
4. `docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_ULTRA_EXECUTION_PLAN_V3.md`
5. `docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_XHIGH_IMPLEMENTATION_HANDOFF_V2.md`
6. `docs/notion_parity/core_free_web_ui/internal_collaborative_packets.v2.json`
7. `docs/notion_parity/core_free_web_ui/internal_collaborative_validation_ladder.v1.json`
8. `docs/notion_parity/core_free_web_ui/internal_collaborative_completion_matrix.v1.json`
9. `docs/notion_parity/core_free_web_ui/internal_collaborative_current_checkpoint.v1.json`
10. `docs/notion_parity/checkpoints/ui-internal-collaboration-o1a-p1-local-backup-restore-20260825.json`
11. `docs/notion_parity/checkpoints/ui-internal-collaboration-v0r-p1-owner-approval-browser-blocked-20260825.json`
12. `docs/notion_parity/core_free_web_ui/internal_collaborative_reference_multiuser_execution.v1.json`
13. `docs/notion_parity/evidence/internal_collaboration_operator/ap-reference-multiuser-user-approval-20260825.json`
14. V0R verifier 세 개와 대응 테스트:
    - `tools/verify_internal_collaborative_reference_multiuser_execution.py`
    - `tools/internal_collaborative_reference_multiuser_approval.py`
    - `tools/verify_internal_collaborative_reference_multiuser_receipt.py`

중요한 해석:

- stable current checkpoint pointer는 마지막 통합 완료 패킷인
  `O1A-P1`을 가리킨다. 이는 정상이다.
- V0R blocked checkpoint는 통합 완료 pointer가 아니라 다음 외부 실행
  커서다. `V0R-P1`이 완전히 검증되기 전에는 stable pointer를 바꾸지
  않는다.
- immutable execution manifest 내부에는 아직
  `awaiting_owner_approval`이라는 준비 시점 상태가 남아 있다. 이를
  고쳐 쓰지 않는다. 별도 approval receipt와 blocked checkpoint가 현재
  승인 사실을 증명한다.

### 6.1 현재 bind된 핵심 hash

| 파일 | SHA-256 |
|---|---|
| `AGENTS.md` | `452b14964e22cc02ac0bd5ad803d991ae30748ceaa7352191ace10f0fe03d996` |
| Ultra plan v3 | `35de7238678c68ea889e271f3107c170fc233cefb667d455a34db42484e71377` |
| xhigh handoff v2 | `abaa2cc047e2b00e6e6d4abe1521743a3e0427eb1f6a2c64e4a7704254968912` |
| product decision | `19b550449db5af3510f62be6b56f65372faeacdd9f10136aa382b73da84362a8` |
| packet catalog v2 | `b0f964b42f6bed61b180e70db3017346f51d9de33f9008945adc4c9f50c0a5fa` |
| validation ladder | `a503e03221d5082c5d9cac2e2d17bd3022f5c00de2017cbb6bdd574e6d0963a5` |
| completion matrix | `50c212a8c539718d7cbb24375d5f08b5bd35c68354bb9d4d28b3e4c29b5f3124` |
| stable checkpoint pointer | `c2f05b8c4cd7ca00d08bbff211bfee1587f90df941fd0c9486cf5ac4a7f3bec9` |
| O1A-P1 checkpoint | `e71373cbacda8cdf00e47ba38ccc374dd78cdb6dcc8a1074b1eee5810f84b3a7` |
| V0R blocked checkpoint | `4197fd3c890dba707b80e58ac547f04f9bfa2b206e04277c2a2f1ef143de8b78` |
| V0R execution manifest | `924b3812e81bffc024bd386a5735c69a676f08e5b8c3f8ce52dffc6e0b9d7cf5` |
| AP-REFERENCE receipt | `4d39668562788aadceaaeb58d19d8c04d10714d967a3a3051b0dddea5f6f21a6` |

## 7. 정직한 전체 진행 상태

- catalog packet: 총 51개
- 필수 packet: 50개
- gate 통과 필수 packet: 42개
- 남은 필수 packet: 8개
- phase gate: 20개 중 14개 닫힘, 6개 남음
- 현재 제품 완료: **아님**
- 운영 승격 완료: **아님**
- 픽셀 parity 최종 완료: **아님**
- target backup/restore 완료: **아님**
- 기존 thread goal 완료: **아님**

통과한 구현 범위에는 개발 pair 격리, canonical authority, durable
transport/gateway, IAM/share runtime, live collaboration, convergent editor와
native IME, Second Brain boundary/publication fence, database/file/history/
search/adopted surface, synthetic local backup/restore가 포함된다.

남은 필수 순서는 다음과 같다.

```text
V0R-P1  외부 Notion 합성 다중 사용자 reference 수집·정리
  -> V0B-P1  최종 release candidate의 Core 25 + 다중 사용자 상태
              2 viewport exact pixel/accessibility 검증
  -> O1B-P1  승인된 외부 암호화 backup target과 target-host restore
  -> Q0-P1   load/security/fault/observability gate
  -> R0A-P1  승인된 offline production-copy rehearsal와 release freeze
  -> R0B-P1  10012 default-off 설치 및 gateway health
  -> R1-P1   승인된 내부 canary, outsider negative control, kill switch
  -> Z0-P1   모든 matrix/phase/approval/realm/generation strict closure
```

조건부 `S0B-P1`은 `S0A-P1`이 보호된 SBE 변경이 불가피하다고 증명할
때만 필요하다. 현재는 required가 아니다.

## 8. 지금 즉시 수행할 작업: `V0R-P1`

### 8.1 목적

개발 구현을 바꾸기 전에 실제 Notion의 두 합성 reference workspace에서
다중 사용자 초대, 페이지 공유, 동시 편집, 권한 전환·회수와 해당 UI를
관측한다. 이 결과는 이후 `V0B-P1`에서 로컬 최종 build와 exact RGBA,
interaction, focus, accessibility를 비교하는 기준이다.

사용자는 다음 범위를 이미 승인했다.

> AP-REFERENCE-MULTIUSER 승인. REF-FREE-A/B에서 테스트 계정과 합성
> 페이지만 사용해 초대·공동 편집·권한 회수·화면 캡처를 수행하고,
> 생성 항목은 Trash로 정리해도 됨. 개인 콘텐츠·공개 게시·영구 삭제는
> 금지.

approval receipt가 현재 manifest hash와 일치하는 동안 같은 승인을 다시
요청하지 않는다. 다만 Computer Use/Chrome 도구가 전송, 공유, 초대,
권한 변경, Trash 이동 등 특정 동작 직전 별도 confirmation을 요구하면
그 confirmation은 생략하거나 자동 승인했다고 가정하지 않는다.

### 8.2 새 세션 preflight

1. 현재 작업공간이 실제로 `G:\AI_Agent\DEMOS_CODEX`이며 다음 두
   evidence 경로에 write 가능한지 확인한다. 임의 임시 파일을 남기지
   말고, 만들었다면 즉시 제거한다.
2. Computer Use skill의 `SKILL.md`, `guidance.md`, 필요한 `api.md`,
   `confirmations.md`를 전부 읽는다.
3. fresh `node_repl`에서 `@oai/sky`를 초기화하고 `list_apps()`를 먼저
   실행한다. 정확히 반환된 Chrome window만 선택하며 handle, app id,
   좌표를 추측하지 않는다.
4. 사용자 보고상 별도 fresh session에서 `list_apps` 40개, running 7개,
   window 8개, Chrome window 2개까지 성공했지만 이는 파일로 봉인되지
   않은 참고 정보다. 새 G: 세션에서 다시 관측하고 기록한다.
5. 두 Chrome context가 각각 로그인된 전용 테스트 identity인지, 열린
   workspace alias가 정확히 `REF-FREE-A`와 `REF-FREE-B`인지 read-only로
   확인한다. raw email, cookie, token, local storage, password 또는 개인
   콘텐츠를 evidence에 기록하지 않는다.
6. manifest와 approval receipt hash 및 verifier를 다시 실행한다.
7. workspace/identity alias 불확실성, 개인 콘텐츠 노출, capture 환경
   drift 또는 control 손실이 있으면 mutation 전에 중단한다.

권장 preflight 명령은 프로젝트 상위 작업공간에서 다음과 같다.

```powershell
.\venv\Scripts\python.exe -E code_assist_v1/tools/verify_internal_collaborative_reference_multiuser_execution.py --compact
.\venv\Scripts\python.exe -E -m pytest -c code_assist_v1/pytest.ini -q -p no:debugging -p no:cacheprovider code_assist_v1/tests/notion_collaboration/test_internal_collaborative_reference_multiuser_execution.py code_assist_v1/tests/notion_collaboration/test_internal_collaborative_reference_multiuser_approval.py code_assist_v1/tests/notion_collaboration/test_internal_collaborative_reference_multiuser_receipt.py
```

### 8.3 정확한 11개 operator action

순서를 바꾸지 않는다.

1. `V0R-PREFLIGHT`: 두 workspace alias와 두 독립 test identity 확인
2. `V0R-CREATE-A`: `REF-FREE-A`에 합성 `V0R-A-ROOT` 생성
3. `V0R-CREATE-B`: `REF-FREE-B`에 합성 `V0R-B-ROOT` 생성
4. `V0R-WORKSPACE-INVITE`: collaborator를 A workspace member로 초대
5. `V0R-PAGE-SHARE`: B 합성 page에 collaborator edit access 부여
6. `V0R-MULTIUSER-CAPTURE`: A에서 presence/text/block/DB/comment/
   mention/reconnect 상태 생성 및 캡처
7. `V0R-PAGE-ROLE-COMMENT`: B page를 comment-only로 변경·캡처
8. `V0R-PAGE-ROLE-VIEW`: B page를 view-only로 변경·캡처
9. `V0R-PAGE-REVOKE`: B page 접근 회수 및 non-oracle 상태 캡처
10. `V0R-WORKSPACE-REVOKE`: A workspace 접근 회수 및 non-oracle 상태 캡처
11. `V0R-CLEANUP`: revoke 완료 후 두 합성 root를 Trash로 이동하고 활성
    test grant/root가 0인지 확인

성공 또는 실패와 관계없이 cleanup을 시도하되 영구 삭제하지 않는다.
browser/control certainty를 잃었다면 과거 좌표나 element index로 정리를
추측하지 말고 현재 상태를 재관측한 뒤 안전한 범위에서만 진행한다.

### 8.4 캡처 denominator

manifest의 `V0R-MU-001`부터 `V0R-MU-018`까지 18개 상태를 각각
`1440x900`, `1280x800`에서 캡처하여 정확히 36개 PNG를 만든다.

- share invite ready, active member, collaborator workspace visibility
- page edit access, inherited workspace access 설명
- two-principal presence, remote cursor/selection
- simultaneous text, block, database convergence
- comment/reply/reaction, mention inbox, reconnect catch-up
- comment-only, view-only
- page revoke 및 workspace revoke의 non-oracle 상태

각 캡처에는 manifest가 지정한 state, workspace/actor alias, viewport,
environment hash, screenshot hash, reference build, focus,
accessible name/role/state, interaction outcome, capture time을 기록한다.

PNG 디렉터리:

`code_assist_v1/docs/notion_parity/evidence/internal_collaboration_reference/v0r-p1/screenshots`

최종 receipt:

`code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/v0r-p1-l6-reference-multiuser-20260825.json`

날짜가 20260825인 경로는 동결 계약이므로 20260826으로 바꾸지 않는다.
스크린샷 파일명은 verifier가 허용하는 고유한 in-root PNG로 만들고,
receipt 작성 전에 verifier 구현을 직접 읽는다. UI 관측만 성공하고 PNG,
환경 receipt, action log, cleanup receipt 또는 hash가 빠졌다면 gate를
통과했다고 주장하지 않는다.

최종 검증:

```powershell
.\venv\Scripts\python.exe -E code_assist_v1/tools/verify_internal_collaborative_reference_multiuser_receipt.py --compact
```

검증이 green인 뒤에만 새 V0R gate checkpoint와 packet catalog/validation
ladder/completion matrix/current pointer의 status integration을 하나의
predeclared DOC_HARNESS packet으로 수행한다. 기존 blocked checkpoint를
성공한 것처럼 덮어쓰지 않는다.

### 8.5 절대 금지 및 stop 조건

- `REF-FREE-A/B` 외 workspace mutation
- `REF-OWNER/REF-COLLABORATOR` 외 identity 초대 또는 메시지
- 기존 page 재사용, 개인 page/workspace 관측·수정
- public publish/link
- 영구 삭제
- production 또는 Second Brain mutation
- raw identifier, DOM dump, network body, cookie, token, storage persistence
- 브라우저 identity, alias, capture 환경, control certainty가 사라진 상태의
  계속 실행

## 9. `V0R-P1` 이후 실행 계약

### 9.1 `V0B-P1`

V0R reference와 이미 구현된 final dev source를 동일 release candidate,
schema/protocol lineage, browser/build/config fingerprint에 bind한다. Core
25와 채택한 모든 multi-user state를 두 viewport에서 재실행한다. masking
없는 deterministic pixel은 unexplained difference 0이어야 하며, focus,
scroll geometry, keyboard path, name/role/state/live region도 함께 닫는다.

### 9.2 `O1B-P1`

local synthetic backup/restore `O1A-P1`은 통과했지만 target 작업은 아직
안 했다. 암호화된 off-host destination, key owner, retention, target-host
new-root restore는 `AP-OPS-TARGET`과 현재 SBE target-operations ownership
handoff 후에만 실행한다. RPO/RTO를 실제 측정하고 Notion/SBE authority를
하나의 generation/high-water manifest로 묶는다.

### 9.3 `Q0-P1`

100/30/10 부하 바닥, reconnect storm, SQLite busy, queue/backpressure,
disk/store/gateway fault, attachment concurrency, CSRF/Origin/ticket/replay/
revocation/non-oracle/upload abuse를 실행한다. p50/p95/p99, memory, queue
lag, compaction age, convergence mismatch를 기록한다.

### 9.4 `R0A-P1`과 `R0B-P1`

- `AP-PROD-REHEARSAL` 전에는 실제 production-data copy를 만들지 않는다.
- 승인된 offline copy에서 migration, restore, redacted smoke, full Python/
  Node suite와 rollback/DR을 검증하고 release candidate를 동결한다.
- `AP-PROD-DEPLOY` 후에만 같은 artifact를 `10012`에 default-off로
  설치하고 gateway service health를 확인한다. 이 단계는 사용자
  enablement/canary가 아니다.

### 9.5 `R1-P1`과 `Z0-P1`

`AP-PROD-CANARY`가 두 허용 collaborator, outsider negative control,
workspace, 기간, abort threshold, kill switch, rollback을 정확히 지정한
뒤 canary를 실행한다. 마지막 `Z0-P1`이 28 matrix row, 20 phase gate,
모든 승인과 realm derivation, release/deployment generation binding,
strict no-skip/no-xfail/no-open-decision 조건을 확인해야 제품 계약이
닫힌다.

## 10. Goal과 완료 선언

기존 thread goal 정보:

- thread id: `019ff607-9116-78a1-9f40-c580c87c0038`
- objective: `이어서 개발 진행해.`
- 마지막 관측 상태: `blocked`
- blocker: 과거 thread의 `node_repl` transport가 영구 종료되어
  Computer Use를 시작하지 못함

이 상태는 제품 구현 blocker가 아니라 이전 thread의 도구 transport
blocker다. 새 thread는 과거 goal 객체를 자동 승계할 수 없으므로,
사용자의 새 kickoff가 명시적으로 goal 생성/재개를 요청하면 현재
thread에서 goal을 확인하고 없을 때 동일 제품 목적의 새 goal을 만든다.

다음 중 어느 것도 완료 선언이 아니다.

- 이 handoff를 읽음
- Computer Use `list_apps` 성공
- V0R 캡처 일부 성공
- 42/50 packet 통과
- 개발 UI가 눈으로 보기 좋음

`Z0-P1`이 닫히고, 그 시점의 `AGENTS.md`가 요구하는 SBE
`TEST_OPERATIONS.md`, `TRACEABILITY.md`, `GOAL_CONTRACT.json` gate도 모두
충족된 뒤에만 전체 goal을 complete로 표시한다.

## 11. 작업 및 증거 규율

- 파일 검색은 `rg`/`rg --files`, 수정은 `apply_patch`를 사용한다.
- shared tree는 다른 개발자가 동시에 수정할 수 있다. 패킷 시작 전에
  exact owned path와 pre-edit hash를 선언하고, 한 파일에는 한 owner만
  둔다.
- unrelated change를 되돌리거나 정리하지 않는다.
- 패킷은 자신의 source/test/evidence만 쓰고, hot status 계약은 선언된
  integrator가 검증된 receipt를 받아 한 번에 갱신한다.
- 외부 UI를 눈으로 봤다는 보고를 binary PNG/hash/action/cleanup receipt
  대신 사용하지 않는다.
- 실패나 중단도 새로운 checkpoint로 남기고 성공 checkpoint를
  덮어쓰지 않는다.
- service restart, identity/root mutation, reference mutation, backup target,
  production copy/deploy/canary/restore는 각각 해당 approval 범위에서만
  수행한다.
- 승인 문장은 test evidence가 아니다. 동작 결과와 cleanup을 별도로
  증명한다.

## 12. 새 메인 에이전트 시작 문구

사용자는 새 로컬 thread에서 다음 문구를 전달하면 된다.

```text
G:\AI_Agent\DEMOS_CODEX를 쓰기 가능한 로컬 작업공간으로 연 메인
에이전트다.

code_assist_v1/docs/notion_parity/core_free_web_ui/
INTERNAL_COLLABORATIVE_NOTION_MAIN_AGENT_HANDOFF_20260826.md를 전체 읽고,
그 문서의 필수 읽기 순서와 현재 hash를 검증해.

이 handoff의 전체 Internal Collaborative Notion 목표를 새 goal로
이어받아 끝까지 수행해. 기존 완료 증거는 재실행하지 말고 검증해서
승계하고, 현재 V0R-P1의 fresh Computer Use preflight와 증거 기록
지점부터 시작해. 필요한 action-time confirmation과 이후 명명된
approval은 그 시점에 요청하고, AGENTS.md 및 다른 SBE 개발의 hot
contract는 수정하지 마. 제품 및 SBE 최종 gate 전에는 goal을 complete로
표시하지 마.
```

## 13. 최초 15분 체크리스트

- [ ] 작업공간 root와 G: write 권한 확인
- [ ] `AGENTS.md` 전체 재독 및 hash 확인
- [ ] goal 상태 확인 후 사용자 kickoff 범위로 새 goal 생성/승계
- [ ] 필수 읽기 순서 완료
- [ ] packet catalog가 42 gate-passed / 8 required planned인지 재검증
- [ ] 10111/10112/10113 health read-only 재확인
- [ ] V0R manifest와 approval receipt hash 재확인
- [ ] Computer Use fresh transport에서 `list_apps` 성공
- [ ] 정확한 두 test identity와 두 workspace alias read-only 확인
- [ ] 36 PNG와 receipt 경로 write 가능 확인
- [ ] 첫 mutation 전에 stop 조건 및 confirmation policy 확인
- [ ] `V0R-PREFLIGHT`부터 정확한 순서로 실행

