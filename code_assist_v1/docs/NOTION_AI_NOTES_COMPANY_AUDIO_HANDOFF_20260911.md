# 사내 구동 handoff: AI 노트 시스템 오디오 · Hynix STT

상태: 로컬 기능/합성 검증 완료, **사내 음성 provider 연결·품질 검증 보류**.
사용자 지시(2026-09-11): 10011 app_code의 연결을 확인하고 음성 처리가 확인되지
않으면 그대로 두고 사내 실행용 handoff로 남긴다.

## 1. 먼저 읽을 문서

1. `COMPANY_OPENAI_COMPATIBLE_HANDOFF.md`: 공통 회사 provider 배포/인증/검증 경계.
2. `NOTION_PERSONAL_MENU_SYSTEM_AUDIO_V3_20260911.md`: 이번 구현, 정확한 STT 설정,
   전송 형식, 개인정보 경계, 검증 결과.
3. `NOTION_AI_NOTES_AUDIO_V2.md`: 기존 전사 큐·저장·재시도 계약. 마이크 전용/단일 STT
   설명은 v3에서 확장됐지만, 당시 증거는 그대로 보존한다.

이 추가 handoff는 공통 회사 모델 정책, SBE 실행 계획, API hot contract, 기존
receipt나 최종 Z0 gate를 수정하지 않는다. 운영 데이터 migration/feature gate 전환을
허가하는 문서도 아니다.

## 2. 10011 app_code 확인 결과

- 실행 프로세스: `code_assist_v1.app_code --port 10011`, PID 30092.
  이 작업에서 수정·재시작하지 않았고 로그인 HTTP 200을 확인했다.
- `app_code.py` → `config.py`/`models.py` → 모델 registry 및 공통 인증 경로를 사용한다.
  standalone의 기본 registry는 `code_assist_v1/api_config.json`; 사내에서는 승인된
  외부 `CODE_ASSIST_API_CONFIG` 경로가 이를 대체할 수 있다.
- 현재 checked-in Gemma: `gemma4-31b` / `gemma-4-31B-it`,
  capabilities = text, analysis, medium.
- 현재 checked-in Qwen: `qwen36-35b` / `Qwen3.6-35B-A3B`,
  capabilities = text, code, medium.
- 두 주소의 path는 `/v1/chat/completions`. 두 모델 모두 `meeting_audio` 설정이 없다.
  app_code/chat 경로에서도 STT 전용 route·오디오 수집·전사 어댑터는 확인되지 않았다.
- 회사 공통 handoff도 text non-stream/SSE/JSON 호환 검사까지 기술하며 STT 지원을
  증명하지 않는다. **채팅 endpoint라는 사실만으로 오디오를 지원하지 않는다고
  단정하지도 않는다. 배포 endpoint의 input_audio 지원 여부가 미확인인 상태다.**
- 기존 모델 ID/토큰 해석 경로는 Notion의 새 음성 어댑터가 재사용할 수 있다.
  텍스트 URL을 STT URL로 자동 복사하거나 모델 명칭만으로 지원을 추정하지 않는다.

비밀값·토큰 파일 본문·회사 원문은 이 조사 결과에 저장하지 않았다.
사내 endpoint에 실제 전사/추론 요청을 보내지 않았다.

## 3. 사내 담당 에이전트 실행 순서

1. 승인된 배포 registry 경로, build/hash, 실행 모드, 정확한 서버 모델 ID를 확인한다.
   공통 회사 handoff의 registry 검증을 먼저 통과시킨다. 회사 URL/토큰은 승인된
   배포 환경에서만 해석하고 저장소/브라우저/회신에 노출하지 않는다.
2. API 문서 또는 운영 담당자에게 Gemma4와 Qwen3.6 **각각**의 음성 입력 지원,
   endpoint, wire schema, 코덱·길이·용량 한도, rate limit, 보관 정책을 확인한다.
3. 현재 `/v1/chat/completions`에서 실제로 input_audio WAV/MP3를 지원하면 해당 모델에
   `meeting_audio.url`을 그 승인 주소로 명시하고 `protocol=chat_audio`로 설정한다.
   이 어댑터는 base64 오디오 입력과 choices[0].message.content 텍스트 응답을 요구한다.
4. multipart 전사 endpoint를 제공한다면 `protocol=multipart`와 실제 URL/model을
   설정한다. 현재 구현 계약과 다르면 **Notion 음성 어댑터에 한정하여** adapter와
   fixture를 추가한다. 공통 텍스트 wire 계약을 음성 때문에 약화하지 않는다.
5. 등록 방법은 v3 문서의 `meeting_audio` 또는
   `CODE_ASSIST_NOTION_STT_GEMMA4_*` / `CODE_ASSIST_NOTION_STT_QWEN36_*`를 사용한다.
   모델별 오디오 전용 인증이 없으면 기존 공통 모델 인증을 재사용할 수 있다.
   누락된 명시적 인증을 다른 계정/토큰으로 우회하지 않는다.
6. 승인된 개발/검증 환경에서 Notion 프로세스에 설정을 주입하고 재시작한다.
   10011을 STT proxy로 거칠 필요 없이 동일한 회사 provider 설정을 서버에서 사용한다.
   기존 로그인·문서 데이터·SBE worker는 바꾸지 않는다.
7. 격리된 테스트 사용자/페이지와 공개 또는 동의된 합성 음성으로 아래 gate를 실행한다.
   현재 로컬 사용자 페이지나 실제 직원 회의를 테스트 fixture로 쓰지 않는다.
8. 반환 증거가 통과한 후에만 회사 STT 연결을 완료로 기록한다. 접속 설정의 존재나
   capabilities.enabled=true는 실제 전사 성공/음질/정확도 증거가 아니다.

## 4. Codex fallback: 구현된 부분과 미구현 부분

- 텍스트 요약/번역 fallback: 회사 Gemma4 → Qwen3.6 → 인증된 Codex.
  `--codex`와 CLI 인증 확인이 필요하다. 현재 10322 로컬 프로세스에서는 이 옵션과
  인증 확인이 활성화돼 있다. 10011의 기존 실행 옵션은 바꾸지 않았다.
- STT fallback 후보 연결점: `CODE_ASSIST_NOTION_STT_CODEX_URL/MODEL/PROTOCOL`.
  loopback 주소 + 명시적 오디오 지원 gateway 설정 + `--codex` + 인증 확인을 모두
  요구하며, 앞선 회사 경로 실패 후 실행 직전에도 연결을 다시 확인한다.
- **기존 `codex_bridge.py`는 codex exec 텍스트 bridge이며 음성 전사 엔진이 아니다.**
  오디오 지원 gateway 자체는 아직 없다. 해당 변수를 임의의 텍스트 URL로 채우지
  말고 미설정 상태로 둔다. 실제 지원되는 Codex 음성 인터페이스가 확보되면 별도
  bounded adapter를 구현·검증해야 한다. 공개 STT 서비스나 다른 모델을 몰래
  Codex라는 이름으로 대체하지 않는다.
- 사내에서 Codex 연결이 금지되거나 없으면 fallback 호출 수는 0이어야 한다.
  정책을 우회해서 개인 계정으로 회사 회의를 전송하지 않는다.

## 5. 사내 acceptance 및 회신 증거

| 항목 | 필수 확인 |
| --- | --- |
| 전사 wire | Gemma4 단독, Qwen3.6 단독 WAV/MP3 또는 multipart 실제 성공; 한국어·영어·혼합·무음 |
| fallback | Gemma 성공 시 1회만; Gemma 실패→Qwen; 둘 다 실패·Codex 미연결이면 명시적 실패; 연결 없는 Codex 호출 0 |
| 시스템 수집 | 대상 Windows/browser에서 탭 소리, 전체 시스템 소리, 시스템+마이크; 실제 track 지원 결과 구분 |
| 중단 | 취소·음성 없는 선택·공유 중단·마이크 거절·권한 회수 때 수집 해제; 영상 전송 0 |
| 저장 | 구간 순서, 마지막 partial 구간, 실패 재시도, 저장 실패 재시도 중 전사 중복 0 |
| LLM | 원문 유지, 인용 근거 존재, 잠정/최종 분리, 선택 언어 요약, 환각 결정·담당·기한 점검 |
| 운영 | 12초 chunk 대비 처리속도/큐 지연, 긴 회의 한도, 429/5xx/timeout, 토큰/원문 로그 유출 0 |

회사 검토자가 정한 corpus/기준으로 한국어 CER·영어 WER, 번역 충실도, 지연 p50/p95/p99,
오류율과 backlog를 측정한다. 지원하지 않는 모델은 unsupported로 명시하고 성공으로
간주하지 않는다. 결과는 새 evidence 경로에 만들고 기존 evidence는 덮어쓰지 않는다.

반환할 정보: build/config hash, 모델 ID/버전, protocol, 지원/미지원 판정, 익명화된
case별 상태·시도 순서·지연·권한 위반/중복 횟수, 검토자/UTC, accepted/conditional/
blocked 결정. 토큰, credential 경로, 회사 원문/녹음 본문, raw exception은 제외한다.

## 6. 현재 승계할 로컬 증거

- 서버/API/권한/경로/계약 회귀 70 passed; 마지막 보강 후 관련 53 passed.
- 브라우저 19 passed / 209.80초, Node 5 suites passed.
- 시스템/마이크 입력은 합성 oscillator와 실제 browser AudioWorklet로 검증했다.
- 로컬 10322 재시작 및 /healthz ok, 10011 로그인 정상. 실제 사용자 문서는 저장 완료
  후 새로고침하면 개인화 `/` 메뉴와 새 소리 선택 UI가 적용된다.
- STT는 여전히 disabled. 코드와 합성 검증을 다시 처음부터 만들 필요는 없지만,
  **실제 사내 음성 provider의 연결·호환성·정확도 gate는 아직 수행해야 한다.**

사내 에이전트 시작 문구:

```text
NOTION_AI_NOTES_COMPANY_AUDIO_HANDOFF_20260911.md와 지정된 읽기 순서를 확인해.
기존 app_code의 승인된 회사 모델/인증 설정을 재사용할 수 있는지 확인하고,
실제 Gemma4/Qwen3.6 오디오 지원 계약에 맞춰 Notion STT 연결을 검증해.
미지원이면 unsupported로 기록하고 임의 endpoint나 대체 모델을 만들지 마.
Codex는 실제 연결·음성 지원이 모두 확인될 때만 STT fallback을 구현/사용해.
합성/승인 테스트 corpus로 새 증거를 남기고 AGENTS/SBE hot contract,
공통 모델 정책과 기존 receipt는 수정하지 마. 사내 gate 전에는 완료로 표시하지 마.
```

## 7. 후속 추가 검사: Codex argument만으로 활성화 가능한가

`NOTION_CODEX_AUDIO_CAPABILITY_PROBE_20260911.md` 참조. 설치된 Codex 0.153.4의
별도 App Server에서 실험적 오디오 인터페이스는 확인했지만, 현재 CLI 인증으로
websocket realtime을 시작하면 `realtime conversation requires API key auth`가
발생했다. 기존 `--codex`는 이미 활성화되어 있다. 옵션만으로 STT를 켤 수 없으며,
이전의 텍스트 fallback/회사 우선순위는 그대로 유지한다. 실제 오디오 업로드나
전사는 수행하지 않았고 STT는 비활성 상태다. 이 추가 검사는 1~6절의 기존 증거를
덮어쓰거나 전사 완료로 승계하지 않는다.

## 8. 후속 사용자 승인: 로컬 Whisper 테스트

`NOTION_LOCAL_STT_TEST_20260911.md`에 명시적 로컬 테스트 모드의 설치·연결·검증을
추가했다. 이 모드에서는 회사/외부 STT 대신 PC의 faster-whisper를 사용한다.
Codex 음성 인증 문제가 해결됐다는 뜻이 아니며, 사내 Gemma4/Qwen3.6 오디오 지원
확인은 여전히 별도 gate다. 기본 회사 provider 정책과 위 역사적 증거는 유지한다.
