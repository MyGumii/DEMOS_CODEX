# Codex 음성 활성화 요청: 로컬 capability 검사 추가 증거

날짜: 2026-09-11. 판정: **현재 인증/어댑터로 활성화 불가, STT 비활성 유지**.

사용자 요청: `--codex` bridge argument가 활성화되어 있다면 음성 기능을 활성화한다.
이 증거는 `NOTION_AI_NOTES_COMPANY_AUDIO_HANDOFF_20260911.md`의 Codex 음성
미연결 상태에 대한 추가 조사다. 이전 구현/합성 검증 증거를 대체하거나 음성 인식
실행 성공으로 재해석하지 않는다.

## 확인 범위와 결과

- 10322는 `python -u -m code_assist_v1.app_notion --port 10322 --codex`로 실행 중이다.
  옵션 누락 때문에 비활성화된 것이 아니다.
- 기존 `codex_bridge.py`의 실행 경로는 `codex exec`의 텍스트 prompt/stdin이다.
  로컬 `exec --help`에는 이미지 입력이 있지만 오디오 입력 옵션은 없다.
- 설치된 CLI는 `codex-cli 0.153.4`. `realtime_conversation`은 under development이며
  기본 false다. 전역 설정을 바꾸지 않고 검사 프로세스에만 해당 기능을 켰다.
- 설치 버전의 `app-server generate-json-schema --experimental` 결과에는
  `thread/realtime/start`, `appendAudio`, `stop` 및 transcript notification이 존재한다.
  따라서 **Codex에 음성 인터페이스 자체가 전혀 없다는 주장은 정확하지 않다.**
- 별도 stdio App Server를 실행해 initialize, listVoices, ephemeral read-only thread,
  realtime start/stop을 호출했다. 실제 마이크/시스템 소리/오디오 파일/회의 텍스트는
  보내지 않았고, workspace 도구 실행이나 에이전트 turn을 요청하지 않았다.
- `initialize`, `thread/realtime/listVoices`, `thread/start`는 성공했다.
  `thread/realtime/start`의 RPC 성공 응답만으로 연결 성공을 판단하지 않았다.
  그 직후 발생한 비동기 `thread/realtime/error`를 판정에 사용했다.

| 요청 조합 | 비동기 결과 |
| --- | --- |
| 기본 version, text output, websocket | `realtime conversation requires API key auth` |
| v2, text output, websocket | `realtime conversation requires API key auth` |
| v3, text output, websocket | `text realtime output modality requires realtime v2` |
| v3, audio output, websocket | `realtime conversation requires API key auth` |

각 검사 끝에 `thread/realtime/stop` 성공을 확인하고 해당 검사 프로세스 트리만
종료했다. 실제 음성 업로드 0, 전사 품질 검증 0, 사용자 페이지 변경 0.
기존 데스크톱 앱의 음성 세션/로그인/전역 Codex 설정과 10011/10322 실행 설정을
변경하지 않았다. 요약/텍스트 Codex fallback은 기존 상태를 유지한다.

## 설정 교차 확인

- standalone 서버가 사용하는 package registry의 Gemma4/Qwen3.6에 명시적
  `meeting_audio` 설정이 없는 상태는 이전 handoff와 같다.
- 비교용 root `api_config.json`의 10개 모델에도 `meeting_audio`는 없다.
  root의 text/vision 모델 목록을 standalone의 Gemma4/Qwen3.6 목록으로 혼동하지
  않는다. URL path `/v1/chat/completions`만으로 음성 지원 여부를 단정하지 않는다.
- 검사 프로세스에서 STT 관련 환경변수 이름이 발견되지 않았다. 기존 실행 중인
  다른 프로세스의 전체 환경/credential을 덤프하지 않았다.

## 남은 연결 조건

현재 CLI 인증만으로 이 App Server websocket 음성 세션을 시작할 수 없다.
API key를 확보해도 기존 `codex exec` bridge가 자동으로 STT bridge로 변하지 않는다.
승인된 실제 음성 경로와 별도 어댑터 구현/검증이 모두 필요하다.

1. 사내 승인된 Gemma4 → Qwen3.6 오디오 API를 연결하거나, 실제 지원되는 Codex
   음성 인증/transport를 확보한다. 앱에서 음성 버튼이 보이는 사실을 해당 CLI
   websocket 경로의 인증 증거로 대체하지 않는다.
2. 추가 과금/계정 인증 변경/회사 회의의 외부 전송은 별도 확인한다. API 키나
   credentials를 문서·브라우저·대화에 복사하지 않는다.
3. App Server 어댑터를 구현한다면 실제 audio encoding, user transcript만 채택,
   turn/도구 실행 금지, 자동 handoff 차단, final transcript 경계, 취소/권한 회수,
   bounded timeout/queue/cleanup을 검증한다. 이 검사는 그 구현을 완료한 것이 아니다.
4. 회사 우선순위와 연결된 Codex만 fallback 허용하는 기존 정책을 유지한다.
   비연결/인증 실패 상태에서 녹음 버튼만 강제로 켜지 않는다.

## 재현 도구 및 출처

검사 스크립트: `.tmp/probe_codex_realtime_20260911.py` (개발 검사 전용).
생성 schema: `.tmp/codex-audio-schema-f44180817f7c41d89eba2ffb7cdd60a3/`.
실행 조합: `--start`, `--start --v2`, `--start --v3`,
`--start --v3 --audio-output`.

OpenAI Docs 스킬에 따라 공식 설명과 설치 버전의 실제 schema를 대조했다.
[App Server 문서](https://learn.chatgpt.com/docs/app-server)는 버전별 schema 생성과
프로토콜 초기화 절차의 근거다.
[Voice 문서](https://learn.chatgpt.com/docs/features/voice)는 앱 음성과 일반 작업의
사용량/기능 경계를 설명하지만, 이 CLI websocket 인증의 성공을 보증하지 않는다.
위 오류와 활성화 불가 판정은 이 기기의 실제 검사 결과다.
