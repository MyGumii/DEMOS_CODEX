# AI 노트 언어 확장 · 호스트 전용 비교 수집 · 입력 UI 정리

상태: 코드/격리 검증 완료, **현재 10322 실행 서버 재시작·비교 수집 활성화 대기**.
이 문서는 Notion 소유의 별도 애플리케이션 확장 증거이며 SBE phase/gate 변경이 아니다.

## 사용자 결정

- 기존 요약 언어 제한을 넓히고 Second Brain 지식의 효과를 비교할 수 있게 한다.
- 확인 답변: **일반 요약과 지식 연동 요약 모두 사용자 화면에 표시하지 않는다.**
  같은 입력으로 생성한 두 결과를 호스트에만 보관한다. 수집 사실은 안내한다.
- 요청한 장문 안내 세 가지 및 참석자 녹음 확인 체크박스를 제거한다.
- 음성 → faster-whisper 원문 → Gemma4/Hynix 등의 텍스트 번역은 가능한 구성이다.
  이번 언어 설정은 **요약/결정/할 일의 출력 언어**이며 원문 전체 번역기나
  실시간 통역 기능을 추가했다는 의미는 아니다.

## 언어 및 화면

- 기존 지침/한국어/영어/일본어/중국어 선택 유지. `직접 입력 · 다른 언어/병기`
  추가: 80자 이하 언어명·코드·병기 요청. 제어문자 거부. 실제 품질/지원은 모델에 따른다.
- 원문과 source_quote, 담당자/기한의 원문 근거는 보존한다.
- 요약 설정의 `Second Brain 비교 수집 · 두 요약 모두 호스트 전용` 선택 후
  본인 개인 Wiki와 선택적 검색어를 지정한다. 수집 시작 시 별도 안내를 확인한다.
- 비교 모드는 최종 원문으로만 처리한다. 회의 중 30초 잠정 요약은 생성하지 않는다.
  자동 처리를 켰다면 회의 종료 시 접수하고, 껐다면 `비교 수집 시작`을 직접 누른다.
- 브라우저에는 접수 ID/상태만 반환한다. 두 결과는 블록 props, 본문, 브라우저
  다운로드에 넣지 않는다. 기존 수동 요약 데이터는 자동 삭제하지 않는다.
- 상태는 새로고침 뒤에도 재조회한다. 브라우저를 닫아도 호스트 작업은 계속된다.
- 일반 요약 모드는 기존 기능으로 남는다. 비교 모드의 결과가 일반 요약으로
  fallback되어 화면에 노출되는 경로는 없다.

## 입력 시작 의미

- 참석자 확인 체크박스 없음. 서버가 준비되면 시작/파일 선택 버튼을 사용할 수 있다.
- 버튼 클릭/파일 선택 전에는 새 녹음·업로드가 시작되지 않는다.
- 기존 전사 wire 필드 `consent=true`는 호환상 유지하되 이번 UI에서는 사용자의
  명시적인 입력 시작을 나타낸다. 제3자 참석자의 동의를 확인했다는 주장으로
  자동 설정하지 않는다. 브라우저의 실제 기기/화면 공유 권한 절차는 유지한다.
- 로컬 전사 기본 자동 요약 OFF 정책은 유지한다. 종료 안내는 `전사 완료`로 줄였다.
- UI에서 제거한 문구는 기존 증거 문서의 과거 기록까지 소급 삭제하지 않았다.

## 지식 및 모델 경계

- 현 호스트의 파일 Wiki에서는 인증된 현재 사용자 자신의 기존 sources만 사용한다.
  다른 사용자/default Wiki를 탐색하지 않는다. 없는 Wiki를 대체 생성하지 않는다.
- canonical host에서는 기존 KnowledgeBridge의 개인 Wiki 목록/권한/검색/문서
  서비스를 소비한다. core가 켜졌지만 읽기 gate/서비스가 없으면 fail closed하며
  파일 Wiki로 fallback하지 않는다. **Part/Team/담당 Wiki는 이번 선택 범위에 없다.**
- 임의의 클라이언트 참고 본문/다른 actor ID를 사용하지 않는다. 서버에서 검색하고
  발췌 총량을 96 KiB로 제한하며 모드, 절단, 문서/버전/발췌 해시를 기록한다.
- 검색어 미지정 시 전체 회의 입력의 빈도 기반 단어를 사용한다. 이 검색 휴리스틱이
  완전한 의미 검색이나 관련 자료 발견을 보장하지는 않는다.
- 관련 문서가 없으면 `no_evidence`로 기록하고 두 LLM 호출을 생략한다.
- 동일 원문·메모·지침·언어·모델 ID/temperature 0.1로 일반/연동 두 arm을 생성한다.
  한 arm 실패 시 다른 모델의 결과와 섞지 않고 다음 후보에서 두 arm을 다시 실행한다.
  회사 Gemma4 → Qwen3.6 → 연결된 Codex의 기존 등록/연결 정책을 유지한다.
- 요약/결정/할 일은 회의 근거로 제한하고, Wiki 배경·해설은 별도
  `knowledge_insights`에 구분한다. 문서 ID와 정확한 인용문을 검증한다.
- 인용문 일치는 의미적 정확성 증명이 아니다. `quality_verified=false`로 기록한다.
  고정 baseline-first 실행 순서 및 latency도 기록하므로 벤치마크 순서 효과를
  숨기지 않는다. 실제 정확도/품질 우위와 사내 API 성능은 별도 평가 대상이다.
- 계정·페이지 편집·Wiki 접근·참고 버전을 호출 전/각 시도/완료 전에 재확인한다.
  권한/버전 변경 시 성공 처리하지 않는다.

## 호스트 저장소와 운영

- 새 별도 SQLite ledger. canonical DB, Wiki/Notion 본문/검색에 기록을 넣지 않는다.
- 원문·지침·참고 발췌·두 결과·모델/시간 정보는 기존 영속 host secret에서 목적별로
  파생한 Fernet 키로 암호화한다. SQL 평문은 임의 ID, HMAC owner, 상태·시간 등이다.
  키가 바뀌면 기존 DB를 다른 키로 덮어쓰지 않고 실패한다.
- DB와 host secret 모두 OS 접근권한/백업 보호가 필요하다. 동일 OS 관리자에게서
  보호하는 DRM이나 별도 KMS를 구현한 것은 아니다.
- 같은 actor/operation UUID는 재실행하지 않는다. SQLite claim/fence로 소비자를
  직렬화한다. 재시작 후 queued는 처리 가능하며 만료된 processing은 interrupted로
  기록하고 자동 재호출하지 않는다. 최대 claim 30분, worker 한 개/프로세스.
- 최대 100개 기록, 암호화 payload 총량 256 MiB 및 사용자당 대기 10개 제한.
  자동 삭제/보존기간 purge는 없다. 한도 도달 시 수집을 거부하므로 호스트 운영자가
  별도 보존/백업 정책을 결정해야 한다. 노트 비우기는 ledger 삭제가 아니다.
- browser용 결과 본문/목록/다운로드 endpoint 없음. 인증된 본인에게 자기 job의
  ID/상태만 제공한다. 다른 사용자의 job은 404. 모든 응답은 no-store.
- 서버 secret이 있는 호스트에서만 아래 도구로 분석용 JSONL을 새 파일에 내보낸다.
  `--output`은 덮어쓰지 않는다. 내보낸 파일은 **민감한 평문**이므로 개인 경로에서
  관리한다. `--output`이 없으면 본문 없이 상태별 개수만 출력한다.

```powershell
G:\AI_Agent\DEMOS_CODEX\venv\Scripts\python.exe -m code_assist_v1.scripts.export_notion_notes_comparisons --db G:\AI_Agent\DEMOS_CODEX\.private\notion-notes-comparisons\comparisons.db
```

실행 디렉터리는 `G:\AI_Agent\DEMOS_CODEX`. `--output <새 절대 JSONL 경로>`를 추가하면
평문 비교 자료를 내보낸다. 백업은 쓰기 정지 후 또는 SQLite online backup으로 하고
해당 host secret도 보존한다. 이번 작업에서 실제 사용자 기록을 수집/내보내지 않았다.

## 활성화 및 남은 실행 단계

- 기본 `CODE_ASSIST_NOTION_NOTES_COMPARISONS=0`: 새 DB/worker를 시작하지 않는다.
- 일반 배포: 위 변수를 `1`, `CODE_ASSIST_NOTION_NOTES_COMPARISON_DB`를 비공개
  절대 DB 경로로 설정하고 Notion 서버를 시작한다. 전역 SBE flag를 변경하지 않는다.
- 현재 로컬 STT launcher에 `--comparisons` 옵션을 추가했다. 녹음·전사·저장이 끝난
  뒤 기존 launcher를 종료하고 다음 명령으로 재개한다. 점유 포트를 강제로 종료하지
  않으며 10011 로그인/SBE 서버는 그대로 둔다.

```powershell
G:\AI_Agent\DEMOS_CODEX\venv\Scripts\python.exe G:\AI_Agent\DEMOS_CODEX\code_assist_v1\scripts\run_notion_local_stt_test.py --comparisons
```

- 이 옵션은 `.private/notion-notes-comparisons/comparisons.db`를 사용한다.
- 이번 turn에서는 사용자 녹음 보호를 위해 launcher/10322/10323을 재시작하지
  않았다. 10011/10322/10323 PID 30092/29296/37884 유지. 실제 ledger도 아직 없다.
- JS/CSS cache tag는 각각 `v20260911h`, `v20260911f`. **서버 재시작 후** 새로고침한다.
- 실제 회사 모델 접속 및 실 사용자 Wiki 비교 품질 검증은 수행하지 않았다.

## 검증 증거

- 서버/모델/음성/로컬 STT 회귀 합계 **85 passed in 50.47s**.
- 수집 동의 메타데이터 반영 후 비교 전용 최종 재검증 **19 passed in 15.43s**.
- 추가/강화 검증: host-only 응답, 암호화, 같은 모델 pair, 다음 모델 pair 재시작,
  중복 요청/다른 payload 충돌, 계정/문서 권한 회수, no_evidence, 만료 fence,
  실제 두 프로세스의 단일 claim, 브라우저 요청 context 없는 worker 처리.
- 브라우저 합본 초기 실행: 24 passed, 상태 조회 재개 1 failed. 행이 DOM에 붙기 전
  polling을 시작하던 원인을 교정했다. 이후 해당 신규 두 시나리오는 **2 passed in
  27.32s**. 이전의 실패를 숨기거나 성공 증거로 덮어쓰지 않는다.
- Node AI notes 및 meeting audio 검증 통과.
- 언어 직접 입력/병기와 선택 저장, 비교 수집 확인 취소/승인, Wiki 추가 페이지,
  새로고침 후 상태 재개, 결과 본문/다운로드 부재, 기존 clear/export/audio 동작 검증.
- `.tmp/notion-notes-comparison-final-20260911/`의 언어 설정 및 호스트 상태 화면을
  시각 검사했다. 모두 격리 synthetic 사용자/문서/음성/모델 응답을 사용했다.
- AGENTS.md SHA-256 `629fe4f841b6aa3ba4ea7b3dd987540e5a3bbcc0bcaf06c2d15f5367e881b763` 유지.
  AGENTS.md, notion.js, SBE hot 계약, 기존 receipt는 수정하지 않았다.
- 전체 제품/SBE 운영 검증 및 최종 Z0 goal 완료 선언은 아니다.
