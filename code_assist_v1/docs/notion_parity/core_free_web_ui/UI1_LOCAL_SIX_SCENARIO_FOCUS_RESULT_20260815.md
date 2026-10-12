# UI1 local golden journey — six-scenario focus result

기준 시각: 2026-08-15 00:11 KST  
프로필: `free-owner-chrome-desktop-ko-KR`  
상태: local six-scenario green, `REF-FREE-A/B` reference pending

## 결과

`UI1-FREE-DESKTOP-GOLDEN-JOURNEY`의 로컬 실제 Chrome 경로가 합성 데이터와 격리된 data root에서 다음 여섯 scenario를 모두 검증한다.

- `UI-SHELL-002`: sidebar 접기·복원과 보이는 control로의 focus 이동;
- `UI-SEARCH-001`: search open, Escape dismiss, origin focus 복원과 synthetic result;
- `UI-EDITOR-001`: 한국어 title/paragraph 저장과 reload exact restore;
- `UI-SB-001`: preview dialog focus, cancel 후 origin focus 복원, Knowledge mutation 0;
- `UI-SB-002`: approve 1회, immutable revision 1회, 동일 snapshot replay 0회;
- `UI-A11Y-001`: `PAGE-ROOT`에서 시작한 logical Tab 순서로 Home, Search, New Page, Second Brain, Page Title을 모두 도달하고 Shift+Tab으로 Second Brain에 복귀.

Second Brain modal은 실제 `dialog`/`aria-modal`/label 관계를 가지며, 열릴 때 modal surface로 focus를 이동하고 닫힐 때 살아 있는 origin control로 focus를 복원한다. 최초 승인과 replay 결과도 modal 내부의 status focus를 유지한다.

## 구현 변경

변경은 UI1의 local hard gate에 필요한 세 파일로 한정했다.

1. `notion_static/index.html`: 공용 modal에 dialog role, modal state, accessible label, programmatic focus target을 추가했다.
2. `notion_static/notion.js`: modal 최초 open 시 origin focus를 기억하고, open/result transition에서 dialog focus를 유지하며, close 시 origin focus를 복원한다.
3. `tests/browser/test_notion_ui1_golden_journey.py`: stale sequential-focus origin을 제거하고 scenario의 `PAGE-ROOT`에서 실제 keyboard traversal을 시작한다. Second Brain focus 별칭을 exact assert하고 cancel/approve/replay 전체 여정을 두 독립 fixture에서 반복한다.

Notion route, schema, migration, auth, Knowledge Core, Second Brain canonical service, production flag와 runtime registration은 수정하지 않았다.

## 로컬 증거

UI source build SHA-256: `sha256:5d348341a46e3e86161b1ff17d93d27f86dda0b1242fdf99eeddc0e20b67f6bf`

| scenario | capture SHA-256 |
|---|---|
| `UI-SHELL-002` | `sha256:0bf0125baccede94f018ab2aee696a4faab67f86f2e66734d00be2211d58a349` |
| `UI-SEARCH-001` | `sha256:73efece016d0326d73bf6f1bbed8b1c2f76caf48e998f7b53102d5bcfb00c0cc` |
| `UI-EDITOR-001` | `sha256:53b2389dc1852262ad19ed7365f7046743d0574faf195049b632b68aee29c938` |
| `UI-SB-001` | `sha256:fe207785ed036bdad8018ed69838cac8e46e1aa09edf8681f4bf3389ca081c8e` |
| `UI-SB-002` | `sha256:f6c8b744785db2f8476cfaeabc33a2f8591f40abe57798cca06e9b796a0f5b7e` |
| `UI-A11Y-001` | `sha256:e4fb7067391ec5ed1f0dac09964f54357709ce1cc1a92f92801e921baa98532d` |

Second Brain aggregate:

```json
{"approve_item_delta":1,"approve_revision_delta":1,"cancel_item_delta":0,"cancel_revision_delta":0,"replay_item_delta":0,"replay_revision_delta":0,"source_snapshot_matches":1}
```

Focus sequence:

```json
["HOME","SEARCH-TRIGGER","NEW-PAGE","SECOND-BRAIN-PREVIEW","PAGE-TITLE","SECOND-BRAIN-PREVIEW"]
```

모든 보고 값은 identifier-free aggregate 또는 고정 synthetic alias다. raw DOM, network log, cookie, token, password, user/page/workspace identifier와 filesystem path를 repository artifact로 저장하지 않았다.

## 검증

- local real Chrome golden journey, shell/editor와 Second Brain 각각 2회 반복: `2 passed in 39.95s`;
- Core Free plan/action/capture/redaction/C0/packet/local fixture: `67 passed in 19.41s`;
- historical evidence/differential regressions: `22 passed in 4.99s`;
- Node UI/editor/publication/atomic-save/CSRF contracts: 6/6 pass;
- Notion routes, atomic-save compatibility, publication adapter: `25 passed in 11.38s`;
- workspace routes, navigation routes, auth isolation: `41 passed in 42.93s`;
- isolated Second Brain publication restart/chat characterization: `5 passed in 4.78s`;
- `node --check`와 Python compile: pass.

소스 격리 확인 중 한 번의 PowerShell invocation이 Windows host 오류 `0xC0000409`로 종료됐다. 명령을 분리한 단일 재검증은 성공했으며, 이후 모든 테스트는 정상 종료했다. 이를 product failure나 pass로 재분류하지 않는다.

## 안전 경계

- 실제 Chrome run은 persistent user profile을 재사용하지 않는 격리 browser context였다.
- mutable data는 pytest가 만든 trusted OS temporary root 또는 별도 `D:\code-assist-pytest-*` temporary directory에만 존재했다.
- personal Notion workspace/content와 live `user_data`, `knowledge`, `.runtime`에는 접근하지 않았다.
- trial, 결제, invite, public posting, 운영 migration, live flag, continuous consumer와 resolver cutover는 수행하지 않았다.

## 남은 gate

이 결과는 local H1 repeatability와 Second Brain hard gate만 증명한다. signed-in Chrome Browser transport가 application/window selection 전에 kernel asset path 오류로 중단된 상태이므로 `REF-FREE-A/B` C0, reference observation, first differential은 아직 없다.

따라서 `live-characterized`, `differential-passed`, H1 complete 또는 Core UI parity complete를 주장하지 않는다. 브라우저 연결이 회복되면 개인 workspace를 열지 않은 상태에서 C0를 먼저 통과하고, 새 Free workspace A/B의 synthetic reference capture를 동일 action ID로 생성해야 한다.
