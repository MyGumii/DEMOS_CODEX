# 번역 개발 접근·저장 위치 및 Codex 실행 인자 교정

> 2026-09-12 후속 요청으로 Codex의 직접 선택 제한을 해제했다.
> 현재 선택/숨김 정책은 `NOTION_CODEX_DIRECT_SELECTION_20260912.md`를 따른다.
> 아래 내용은 각 기록 시점의 감사·실행·cleanup 이력이다.

사용자 요청에 따라 2026-09-11에 확인했다. 실제 페이지는 읽기 전용 SQLite
연결(`mode=ro`, `PRAGMA query_only=ON`)로 해당 page ID만 조회했다.
본문·음성·인증 비밀은 이 기록에 포함하지 않는다.

## 개발·검증 때 사용한 데이터

- 브라우저 테스트 계정: `ui1-owner`, 표시명 `UI1 Owner`.
  실제 계정 등록부가 아니라 격리 fixture의 `.runtime/users.json`을 사용한다.
  테스트마다 임의 비밀번호와 빈 페이지를 만들고, 테스트용 loopback 포트에서만 사용한다.
- 서버 단위 테스트의 `owner`, `collaborator`, `other`도 격리 임시 계정이며,
  실제 로그인 계정으로 해당 문자열을 사용한 것이 아니다.
- 테스트 로그에서 확인 가능한 페이지 예:
  `p_f4e809a232434e27a062edf75a046af8`,
  `p_2cf30978e517481b82ee695285ea417c`,
  `p_9e07e678c82846df9fdac3e582003fc2`.
  이는 테스트 중 생성된 페이지이며 현재 사용자의 Resources 페이지가 아니다.
- 테스트 데이터는 OS 임시 폴더
  `C:\Users\user\AppData\Local\Temp\code-assist-pytest-<run>\pytest-tmp\<test>\isolated\`
  아래에 저장된다. 페이지 DB는 그 아래 `user_data/ui1-owner/notion/notion.db`다.
- 직전 최종 실행의 `code-assist-pytest-wh5fdes0`, `-0cuktbnt`, `-lxurkcyp`,
  `-c6_h1m6h` 및 초기 실패 실행의 `-iwke0ls_` 디렉터리는 현재 존재하지 않는다.
  테스트 session 종료 시 `TemporaryDirectory.cleanup()`으로 합성 DB/계정/다운로드가
  정리되는 fixture 계약과 일치한다. 실제 사용자 파일을 삭제한 것은 아니다.
- 브라우저 음성 검증은 oscillator와 합성 WAV를 사용했고, 실제 마이크·음악·회의 음성을
  캡처하지 않았다. 실제 Codex bridge 호출 1회에는 코드에 직접 적은 영어 합성 문장만
  전달했으며 페이지나 음성 파일을 읽어 만들지 않았다.
- 남겨 둔 화면 증거:
  `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-translation-final-v2-20260912-images\ai-notes-translation.png`.
  기존 요약/전사/비교 회귀 화면은 `.tmp/notion-translation-final-20260912-images/`.
  파일명의 `20260912`는 이전 작업의 구분자이며 실제 저장일은 2026-09-11이다.

## 현재 사용자 탭의 저장 위치 — 개발 테스트 데이터와 구분

- 페이지: `Resources`, ID `p_3a1f17de0eac495aa2d0d64d27b68b35`.
- 페이지 소유자: `admin`. 이것만으로 브라우저의 현재 로그인 사용자를 단정하지 않는다.
- 실제 저장 DB:
  `G:\AI_Agent\DEMOS_CODEX\code_assist_v1\user_data\admin\notion\notion.db`.
- 조회 시 AI 노트 3개. 원문 글자 수는 각각 0 / 2,597 / 0이며 번역은 모두 0자.
  이 페이지에 등록된 첨부파일은 0개였다.
- 원문/번역은 `blocks.props_json`의 `transcript`, `translation_text` 등으로 저장한다.
  이번 조회에서 실제 내용을 화면에 출력하거나 모델에 보내지 않았다.
- STT 원음은 처리용 메모리와 요청 임시 버퍼를 사용하고 영구 녹음 파일을 남기지 않는다.
  사용자가 직접 누른 다운로드는 클라이언트 브라우저가 정한 다운로드 위치에 저장되며,
  서버의 Notion files 폴더에 자동으로 생기는 파일이 아니다.
- faster-whisper 모델 파일은 녹음 파일이 아니다:
  `G:\AI_Agent\DEMOS_CODEX\.tools\notion-stt\models\base\model.bin`.
- 호스트 비공개 비교 DB:
  `G:\AI_Agent\DEMOS_CODEX\.private\notion-notes-comparisons\comparisons.db`.
  이번 읽기 전용 집계에서 비교 실행 레코드는 0개였다. 복호화는 수행하지 않았다.

## 발견한 정책 불일치와 교정

직전 로컬 테스트 구현에 두 예외가 있었다:

1. local STT + 연결된 Codex이면 번역 기본 모델을 Codex로 지정했다.
2. 로컬 STT 실행기가 사용자 인자와 무관하게 Notion 자식에게 `--codex`를 붙였다.

두 예외를 제거한다. 현재 적용 정책:

- 기본은 자동 **Gemma4 → Qwen3.6 → Codex fallback**.
- Codex는 해당 서버 진입점의 `--codex`가 명시되고 CLI 인증도 확인된 경우에만
  서버 모델 후보/화면에 포함된다. 연결되지 않았거나 인자가 없으면 사용하지 않는다.
- 10011과 10322는 별도 프로세스다. 10011 인자만으로 10322의 권한을 추정·활성화하지
  않는다. Notion 실행기에도 `--codex`를 명시해야 한다.
- 로컬 실행기 기본은 OFF. `--codex`를 주었을 때만 자식에게 전달한다.
  app_notion의 기존 부팅 계약이 inherited 환경변수의 오래된 ON도 OFF로 정규화한다.
- 번역 탭 기본은 항상 자동이며 Codex는 직접 우선 선택이 아니라 fallback 표시다.
  기존에 저장된 Codex 선택값도 회사 모델을 건너뛰는 근거로 사용하지 않는다.
- 실행 인자가 OFF이면 Codex 옵션/자동 경로 표시/전용 안내를 숨긴다. 연결된 회사
  모델도 없다면 번역 실행·자동 번역 컨트롤을 비활성화한다. 과거 번역 결과는 보존한다.
- 번역 서버는 Codex fallback 직전에도 연결·활성화 상태를 다시 확인한다.
- 선택한 회사 모델을 직접 지정하는 기존 기능은 유지하며, 그 경우는 해당 모델 요청이다.
  자동 옵션이 위 순서로 전체 fallback을 수행한다.
- 사용자 페이지의 저장된 설정/원문/번역은 마이그레이션하거나 덮어쓰지 않는다.

### 실행 예

Codex 없이 로컬 음성 + 사내 모델만:

```powershell
.\venv\Scripts\python.exe code_assist_v1/scripts/run_notion_local_stt_test.py --comparisons
```

연결된 Codex를 마지막 fallback으로 명시적으로 허용:

```powershell
.\venv\Scripts\python.exe code_assist_v1/scripts/run_notion_local_stt_test.py --comparisons --codex
```

`--comparisons`는 별도의 호스트 비공개 요약 비교 기능이며 Codex 권한을 켜지 않는다.
10011의 기존 `python -m code_assist_v1.app_code --port 10011 --codex` 계약은 변경하지 않았다.

## 검증·실행 반영

- 서버 테스트: 실행 인자 ON/OFF, 인증 ON/OFF, 로컬 STT에서도 자동 기본 유지,
  Gemma/Qwen/Codex 순서, 이전 Codex ID 우회 방지, 중간 연결 해제 및 기존 요약/권한.
  **55 passed / 38.35초**.
- Node: 후보 목록 fail-closed, Codex 기본 선택 방지, 구간/다운로드/비우기/취소 회귀 통과.
- 브라우저: 인자 OFF/ON 및 회사 모델 유무, 기존 Codex 선택값의 자동 전환,
  번역·녹음·요약·비공개 비교 회귀 **10 passed / 139.97초**.
  첫 실행의 두 실패는 disabled option에 대한 Playwright assertion 불일치였으며,
  실제 `disabled` 속성을 검사하도록 테스트를 교정한 후 전체 재실행했다.
  화면 증거는 `.tmp/notion-codex-policy-final-20260911-images/`에 저장했다.
- 재시작 승인: 사용자가 `진행 중 아님 — 재시작 가능`으로 응답했다.
  이전 실행기 PID 29552 및 정확한 자식 트리를 확인하고 해당 Notion/STT만 재시작했다.
  10011은 자식 트리에 포함되지 않음을 확인했고 PID 34392 그대로 유지했다.
- 새 실행기 PID 31972, 명령 `run_notion_local_stt_test.py --comparisons --codex`.
  10322 listener PID 30836, 10323 listener PID 30448. 로그인/Notion health HTTP 200,
  내부 STT 익명 health HTTP 401 확인. STT는 127.0.0.1 bind를 유지한다.
  새 로그는 `.tmp/notion-web-policy-20260911/`다.
- 2026-09-11 23:24 KST 실제 모델 목록을 읽기 전용으로 검사했다. 현재 등록된
  10개 회사 API 모델에는 Gemma4/Qwen3.6 family가 없다. 요청한 family를 다른 Qwen
  버전으로 가장하거나 registry를 변경하지 않았다. 현재 프로세스 정책을 적용한
  후보는 ON일 때 `codex-local` 하나, OFF일 때 빈 목록이었다. 모델 호출은 하지 않았다.
- 공통 `model_routing.py`, `codex_bridge.py`, `internal_llm.py`, `notion.js`,
  AGENTS.md 및 SBE hot contract는 변경하지 않았다.

## 2026-09-11 23:36 KST 임시 파일 재점검 — 삭제 차단, 잔여물 있음

사용자가 번역 개발 임시 파일의 재점검 및 삭제를 요청했다. 앞서 테스트 DB/계정/
다운로드가 자동 정리됐다는 설명은 화면 증거와 실행 로그까지 모두 삭제됐다는 뜻은
아니었다. 아래 잔여물은 별도로 보존되어 있었다.

- OS Temp의 `notion-translation-*` 디렉터리와 번역 개발 시간대(22시 이후)의
  `code-assist-pytest-*` 디렉터리는 발견되지 않았다. 남아 있는 이전 pytest 폴더들의
  `pytest-tmp` 하위에도 번역 및 이번 실행 인자 교정 테스트 이름이 발견되지 않았다.
- workspace `.tmp`의 번역 이름 파일 검색 및 관련 디렉터리 내용 조회 결과,
  이번 번역/후속 정책 회귀 스크린샷 19개와 종료된 이전 Notion 실행 로그 2개를
  정리 대상으로 확인했다. 합계 21개 파일, 528,135바이트다.
- 기본 Downloads 폴더에서 번역/translation/AI notes 이름의 파일은 발견되지 않았다.
  사용자가 직접 지정한 다른 다운로드 폴더나 임의로 이름을 바꾼 파일의 부재를
  보증하는 전 디스크 검사는 아니다.
- 확정된 경로만 삭제하는 PowerShell 명령을 시도했으나 도구가 실행 전에
  `blocked by policy`로 거부했다. 다른 경로로 우회하지 않았다.
  재조회에서 **21개 파일 모두 여전히 존재**했다. 이번 요청으로 삭제된 파일은 0개다.

### 직접 삭제할 수 있는 확정 대상

다음 6개 폴더는 내부 파일이 모두 이번 테스트 화면 PNG임을 확인했다.

1. `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-codex-policy-20260911-images` — 3개
2. `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-codex-policy-final-20260911-images` — 4개
3. `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-translation-browser-20260912a-images` — 3개
4. `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-translation-browser-20260912b-images` — 1개
5. `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-translation-final-20260912-images` — 7개
6. `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-translation-final-v2-20260912-images` — 1개

다음 2개 파일만 삭제 대상이며, 이 상위 폴더 전체는 삭제 대상이 아니다.

- `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-web-20260912a\notion.stderr.log`
- `G:\AI_Agent\DEMOS_CODEX\.tmp\notion-web-20260912a\notion.stdout.log`

보존: 실제 사용자 DB/원문, 비교 DB, 설치된 STT 모델 및 실행기, 개발 코드/테스트/
문서, 다른 작업의 임시 파일, 기존 immutable receipt. 실행 중인 10011의
`notion-web-20260912a\login.*.log`와 현재 Notion/STT의
`notion-web-policy-20260911\notion.*.log`도 보존했다. 서버를 중지·재시작하지 않았다.
