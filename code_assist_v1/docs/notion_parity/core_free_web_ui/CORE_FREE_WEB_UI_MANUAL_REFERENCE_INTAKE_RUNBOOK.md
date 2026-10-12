# Core Free Web UI manual reference intake runbook

상태: 자동 Chrome 제어가 불가능할 때 사용하는 합성 Free workspace 전용
operator-observed evidence 절차. 이 절차는 reference를 추측하거나 parity를
승격하지 않는다.

권위 계획:

- `docs/NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`
- `docs/notion_parity/CORE_FREE_WEB_UI_HARNESS_DESIGN.md`
- `docs/notion_parity/CORE_FREE_WEB_UI_CHARACTERIZATION_RUNBOOK.md`

## 1. 목적과 경계

`ui_reference_intake`는 브라우저를 제어하지 않는다. 사용자가 Chrome에서
직접 관찰한 합성 `REF-FREE-A/B` 상태를 기존 C0, action, capture,
redaction 계약으로 검증하는 보조 경로다.

이 경로가 허용하지 않는 것:

- 개인 workspace 또는 개인 page 열기;
- raw DOM, raw accessibility tree, network log, cookie, storage 수집;
- 실제 workspace/page 이름, ID, URL, email 또는 token 기록;
- invite, public posting, plan 변경 또는 외부 side effect;
- Notion에서 Second Brain 전용 action 실행;
- accepted receipt만으로 `differential-passed` 또는 promotion 주장.

모든 명령은 `G:\AI_Agent\DEMOS_CODEX`에서 project interpreter로 실행한다.
출력 파일은 repository나 live data root가 아닌 새 trusted OS temporary
directory에만 둔다.

먼저 현재 PowerShell 세션에서 전용 임시 디렉터리를 하나 만든다.

```powershell
$refIntakeRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
  ("code-assist-notion-reference-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $refIntakeRoot | Out-Null
```

아래 도구의 `--output`은 이 OS temporary root 아래의 아직 존재하지 않는
`.json` 파일만 허용한다. 기존 파일을 덮어쓰지 않으며 repository 경로나 link,
reparse point를 거부한다.

## 2. C0 — screenshot 없는 읽기 전용 확인

Chrome에서 다음을 육안 확인한다.

1. 로그인된 Notion 화면이다.
2. workspace switcher를 열었다.
3. 미리 정한 합성 workspace A/B를 둘 다 식별했다.
4. 개인 workspace/page를 열지 않았다.
5. screenshot, raw artifact, mutation, 외부 side effect를 만들지 않았다.
6. switcher에 보이는 다른 label을 어떤 파일에도 복사하지 않았다.

그 후 식별자가 없는 C0 descriptor를 생성한다.

```powershell
venv\Scripts\python.exe -m `
  code_assist_v1.tools.notion_parity.ui_reference_intake c0 `
  --run-id ui1-manual-reference `
  --browser-connection-visible `
  --chrome-session-confirmed `
  --signed-in-visible `
  --workspace-switcher-opened `
  --ref-free-a-confirmed `
  --ref-free-b-confirmed `
  --other-workspace-labels-visible `
  --output (Join-Path $refIntakeRoot 'c0.json')
```

실제로 관찰하지 않은 flag는 넣지 않는다. 개인 workspace/page를 잘못 연
경우에는 대응 `--personal-...-opened` flag를 넣어 실패 descriptor를
남기고 즉시 중단한다. C0에서 screenshot을 만들면 실패다.

## 3. 첫 worksheet

첫 수동 reference는 mutation이 없는 `UI-SHELL-002`로 한정한다.

```powershell
venv\Scripts\python.exe -m `
  code_assist_v1.tools.notion_parity.ui_reference_intake worksheet `
  --scenario UI-SHELL-002 `
  --run-id ui1-manual-reference `
  --output (Join-Path $refIntakeRoot 'worksheet.json')
```

출력은 검증된 scenario plan에서 파생된 action 순서와 모든 필수 capture
지점을 포함한다. selector, 실제 label 또는 private ID를 추가하지 않는다.

## 4. 제출 draft

통과한 C0 descriptor를 새 trusted temporary file에 보관한 뒤 draft를
생성한다. `--synthetic-only-confirmed`는 해당 파일이 합성 관찰만 담는다는
operator 확인이며, flag가 없으면 파일을 읽기 전에 실패한다.

```powershell
venv\Scripts\python.exe -m `
  code_assist_v1.tools.notion_parity.ui_reference_intake draft `
  (Join-Path $refIntakeRoot 'c0.json') `
  --scenario UI-SHELL-002 `
  --run-id ui1-manual-reference `
  --synthetic-only-confirmed `
  --output (Join-Path $refIntakeRoot 'submission.json')
```

draft는 의도적으로 검증에 실패하는 상태다. 다음 필드를 실제 관찰값으로
채워야 한다.

- 각 `capture_after_action_id`에 대응하는 capture 한 개;
- viewport와 semantic geometry;
- state, keyboard, focus, a11y, semantic visual, error recovery;
- 측정 latency;
- 동일 reference build를 나타내는 canonical SHA-256;
- 선택적으로 redaction 완료된 screenshot의 SHA-256;
- timezone이 있는 `recorded_at`;
- 사람이 확인한 뒤에만 `capture_reviewed_redacted=true`.

실제 title이나 workspace label 대신 계획에 선언된 semantic alias만 쓴다.
여러 capture가 서로 다른 source-build hash를 가지면 전체 submission이
거부된다.

## 5. 검증

```powershell
venv\Scripts\python.exe -m `
  code_assist_v1.tools.notion_parity.ui_reference_intake verify `
  (Join-Path $refIntakeRoot 'submission.json') `
  --synthetic-only-confirmed `
  --output (Join-Path $refIntakeRoot 'receipt.json')
```

검증기는 다음을 모두 확인한다.

- exact passing C0와 동일 run ID;
- scenario plan과 byte-equivalent action log;
- 선언된 모든 capture 지점의 정확한 순서와 개수;
- reference side/profile/scenario/action-log hash;
- capture source build 일관성;
- allowlist-first redaction;
- synthetic-only operator attestation;
- 개인 접근, raw artifact, network/cookie/storage, public posting 및 외부
  side effect가 모두 false.

accepted receipt에는 synthetic ID, count와 canonical hash만 남으며 path는
노출하지 않는다. `promotion_allowed`는 항상 false다.

## 6. 다음 단계

accepted receipt는 reference capture 후보일 뿐이다. 다음 작업은 별도다.

1. approved temporary root의 redacted artifacts를 sealed evidence packet으로
   묶는다.
2. 같은 action ID를 가진 현재 local capture와 비교한다.
3. exact state/keyboard/focus/a11y/semantic/error 차이를 기록한다.
4. geometry와 latency는 calibration 전까지 measurement-only로 유지한다.
5. evidence run과 분리된 다음 slice에서만 cohesive UI correction을 한다.

`UI-SB-*` action은 Notion reference에서 실행하지 않는다. 계획이 정한 대로
Notion 공통 UI 구간은 reference/local differential로, Second Brain
preview/approve/session 동작은 local hard contract로 검증한다.

## 7. 중단 조건

다음 중 하나면 즉시 중단한다.

- 합성 workspace A/B를 확실히 구별할 수 없음;
- 개인 콘텐츠가 화면에 열림;
- 실제 label/ID/URL을 redaction할 수 없음;
- 계획에 없는 action이나 selector가 필요함;
- invite/public/plan/external mutation이 필요함;
- submission verifier가 실패함;
- source build가 action 사이에 변함.
