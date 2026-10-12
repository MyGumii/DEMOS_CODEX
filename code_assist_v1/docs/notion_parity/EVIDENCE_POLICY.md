# NP-0 시나리오·증거 정책

상태: 초안 승인 기준  
기준일: 2026-08-13 (Asia/Seoul)  
적용 범위: `docs/notion_parity/catalog/`, 향후 `tools/notion_parity/`, `tests/notion_parity/`, 외부 비커밋 실행 증거

이 문서는 Notion 제품과의 제휴·인증 또는 전체 동등성을 주장하지 않는다. 공식 문서와 권한 있는 사용자가 정상 UI에서 관찰한 동작을 독립 구현의 요구사항으로 관리하기 위한 내부 정책이다. Notion API로 서비스를 복제하거나 private endpoint, scraping, rate-limit/security 우회를 사용하지 않는다.

## 1. 시나리오 식별자

시나리오 ID는 `NP<phase>-<AREA>-<BEHAVIOR>-<NNN>` 형식이다.

- `phase`: `0`부터 `11`까지의 정수다.
- `AREA`와 `BEHAVIOR`: 대문자 ASCII와 숫자, 단일 하이픈만 사용한다.
- `NNN`: 같은 behavior 안에서 `001`부터 증가하며 재사용하지 않는다.
- 삭제된 ID는 `retired`로 남기고 다른 동작에 재할당하지 않는다.
- Second Brain 불변 시나리오는 기능 시나리오와 별도로 `SB-*` hard gate를 참조한다.

예: `NP2-SIDEBAR-COLLAPSE-001`, `NP3-EDITOR-ENTER-001`.

## 2. 상태 승격

허용 상태와 최소 증거는 다음과 같다.

| 상태 | 의미 | 필수 증거 |
|---|---|---|
| `unknown` | 공식 문서 또는 실제 UI에서 확인되지 않았다. | 확인할 기준 URL 또는 명시적 미확인 사유 |
| `reference-only` | 공식 문서에 기능이 기술됐지만 실제 UI 상태 전이는 관찰하지 않았다. | 공식 URL, 기준일, plan/role/platform/locale |
| `exists` | 저장소에서 해당 경로가 발견됐다. | 소스 또는 정적 inventory 경로 |
| `contract-tested` | 격리 환경에서 입력·출력·오류·권한 계약을 자동 검증했다. | 테스트 경로와 재현 명령 또는 결과 hash |
| `live-characterized` | 권한 있는 공식 제품 UI에서 시나리오를 직접 관찰했다. | 환경 manifest, redacted action/result, input/result SHA-256, reviewer |
| `differential-passed` | 동일 action script가 공식 기준과 독립 구현에서 허용 오차 이내다. | 양쪽 run hash, 비교 정책 버전, diff report, reviewer |
| `reference-conflict` | 공식 문서·UI·API 기준이 서로 충돌한다. | 서로 다른 공식 source와 관찰 범위 |
| `excluded` | 법률·브랜드·제품 경계 또는 sunset 정책상 구현하지 않는다. | 제외 사유와 승인자 |

machine-readable 증거는 catalog와 동일하게 관찰 단계인 `observation_state`에는 하이픈 표기, 기존 `notion_parity_manifest.json`의 승격 단계인 `promotion_status`에는 underscore 표기를 사용한다. 예를 들어 `live-characterized`의 승인된 승격 값은 `live_characterized`다. 상태는 증거 없이 추론해 승격하지 않는다. 자동 테스트 통과는 live 또는 differential 승격을 의미하지 않는다. 브라우저 세션, 플랜 또는 역할을 확보하지 못하면 해당 차원은 `unknown`으로 남긴다.

## 3. 실행 증거 최소 구조

실행 결과는 다음 필드를 가진 하나의 JSON manifest로 기록한다.

```json
{
  "schema_version": 1,
  "scenario_id": "NP3-EDITOR-ENTER-001",
  "observation_state": "live-characterized",
  "promotion_status": "live_characterized",
  "reference_date": "2026-08-13",
  "reference_sources": ["https://www.notion.com/help/..."],
  "plan": "free",
  "role": "workspace_owner",
  "platform": "desktop_web",
  "app_or_browser_version": "exact-version",
  "locale": "ko-KR",
  "input_sha256": "sha256:<64-lowercase-hex>",
  "result_sha256": "sha256:<64-lowercase-hex>",
  "evidence": ["relative/redacted/path"],
  "reference_conflicts": [],
  "reviewer": "reviewer-id",
  "reviewed_at": "RFC3339 timestamp"
}
```

원문 DOM, 네트워크 payload, 쿠키, 토큰, 사용자 콘텐츠는 manifest에 넣지 않는다. hash는 redaction과 canonical normalization을 마친 결과에 대해 계산한다.

## 4. 수집 허용 범위

- 권한 있는 테스트 계정과 정상 공개 UI만 사용한다.
- 사용자가 직접 로그인한 세션에서 화면에 표시되는 상태, 접근성 트리, focus, keyboard 결과, geometry, 승인된 screenshot만 관찰한다.
- private API, 무단 DOM 대량 수집, CAPTCHA·rate limit·보안 제어 우회, cookie/localStorage/password/session store 열람은 금지한다.
- 실제 조직·개인 workspace 대신 합성 데이터 전용 baseline workspace를 사용한다.
- Enterprise 기능은 승인된 Enterprise 테스트 tenant와 관리자 역할이 없으면 `unknown`으로 둔다.
- Notion Mail inbox처럼 공식 종료가 확정된 별도 제품은 core parity의 live 기준으로 신규 채택하지 않는다.

## 5. Redaction

다음 값은 저장 전에 제거하거나 deterministic placeholder로 치환한다.

- 이메일, 전화번호, 실명, 사용자·workspace·teamspace 이름
- page/database/block/file/public-link/session/conversation의 실제 ID와 token
- cookie, CSRF, OAuth, API key, Authorization header, signed URL과 query secret
- 로컬 절대경로, 사용자 홈, 데이터 root, 원본 파일명
- 외부 서비스 response body와 provider 내부 오류 문자열
- 테스트 목적과 무관한 page 본문, 댓글, 첨부 파일, AI prompt/response

placeholder는 실행별 salt를 사용한 비가역 hash(`user-<hash8>`, `page-<hash8>`)로 만든다. 동일 run 안의 참조 관계만 유지하고 run 간 실제 주체를 연결할 수 없게 한다. screenshot은 허용 영역을 먼저 crop하고 남은 민감 텍스트를 불투명 마스킹한다. blur만 사용하지 않는다.

redaction 검증기는 최소한 다음 패턴을 fail-closed로 탐지해야 한다.

- 이메일·전화번호·Bearer/token/cookie/header 후보
- Windows drive/UNC 및 POSIX home 절대경로
- 32자 이상의 불명 opaque token 또는 signed query
- baseline allowlist에 없는 host와 실제 사용자 이름

탐지 실패나 애매한 결과는 artifact 생성을 중단하고 `redaction_failed`로 기록한다.

## 6. 보존과 삭제

- 저장소에는 catalog, schema, 정책, 합성 fixture, hash와 비민감 요약만 commit한다.
- screenshot, DOM/accessibility snapshot, network trace, video 등 실행 증거는 기본적으로 저장소 밖의 승인된 암호화 경로에 둔다.
- 원시 capture는 검토 후 7일 이내 삭제한다.
- redacted evidence는 기본 30일, release acceptance에 연결된 승인본은 최대 90일 보존한다.
- 법률·보안 incident hold가 있으면 소유자, 사유, 만료일을 별도 기록한다.
- 삭제 시 manifest에 `deleted_at`, `deletion_reason`, `deleted_by`를 남기되 원문을 복구할 수 있는 경로나 token은 남기지 않는다.

## 7. 비교 정책

pixel-perfect 복제를 기본 기준으로 사용하지 않는다. 각 시나리오는 필요한 비교자만 선언한다.

- `state`: page/block/database/permission의 관찰 가능한 전이
- `keyboard`: key sequence, focus target, selection/caret 결과
- `a11y`: role, accessible name/state, focus order, keyboard reachability
- `geometry`: breakpoint와 승인된 px/ratio tolerance
- `semantic_visual`: 자체 design token 기준의 hierarchy, affordance, visibility
- `error_recovery`: status/code/message category, retry/cancel/no-partial-write 결과
- `latency`: 합성 fixture 크기별 percentile budget

Notion 명칭, logo, icon, illustration, onboarding/help 문구와 고유 visual identity는 비교·복제 대상이 아니다. 동작 결과와 접근성 affordance만 독립 design system으로 구현한다.

## 8. 승인과 차단 조건

다음 중 하나라도 해당하면 시나리오를 pass로 승격하지 않는다.

- source/date/plan/role/platform/locale 중 하나가 빠짐
- live 증거 없이 `live-characterized`/`live_characterized` 또는 `differential-passed`/`differential_passed` 사용
- redaction 검증 실패 또는 민감 원문이 저장소에 포함됨
- 공식 source 충돌을 숨기거나 한쪽을 임의 선택함
- Second Brain hard gate 회귀
- 테스트가 live `user_data`, `knowledge`, `.runtime`, `sessions`, `workspace`를 읽거나 변경함
- Notion API 기반 서비스 복제, private endpoint 또는 브랜드 confusing similarity가 필요함

NP-0 전체 gate는 catalog 완전성, verifier repeatability, redaction test, 승인된 browser capture, differential runner, 법률·보안 review가 모두 충족된 뒤에만 완료로 표시한다.
