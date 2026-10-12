# 로컬 Whisper 연결 · 사용자 녹음 테스트

> 후속 교정: 현재 실행기는 Codex를 자동으로 켜지 않는다. 필요한 경우 명시적으로
> `--codex`를 추가한다. `NOTION_TRANSLATION_ACCESS_AND_FALLBACK_AUDIT_20260911.md` 참조.

사용자 승인(2026-09-11): faster-whisper 설치, 연결 후 사용자가 직접 YouTube Music
등의 소리로 녹음 시험. 실제 사용자 녹음·음악 재생은 이 작업에서 시작하지 않는다.

## 구성과 기존 정책 경계

- 음성: 선택한 탭/시스템/마이크 → 기존 12초 WAV 구간 → 인증된 Notion 전사 route
  → 127.0.0.1:10323 로컬 faster-whisper base → 원문 저장.
- 요약: 기존 Hynix Gemma4 → Qwen3.6 → 연결된 Codex 정책. 로컬 STT는 Codex 음성
  엔진으로 표시하지 않으며 기존 모델 registry/공통 bridge를 바꾸지 않는다.
- `CODE_ASSIST_NOTION_STT_MODE=local_test`인 서버에만 로컬 음성 테스트를 적용한다.
  이 명시적 테스트 모드는 음성을 회사/외부 provider로 fallback하지 않는다.
  환경변수가 없거나 `providers`이면 기존 회사 음성 우선순위가 그대로 적용된다.
- 자동 요약은 로컬 테스트에서 기본 OFF. 체크하지 않으면 회의 종료 뒤에도 텍스트를
  LLM에 자동 전송하지 않는다. 사용자가 `요약 생성`을 누르면 기존 요약 경로로 전송한다.
  기존 provider 모드의 기본 ON은 유지한다. 체크박스는 회의 중/종료 후 자동 요약을
  모두 제어하도록 명칭과 동작을 맞췄다.

## 설치 위치와 재현

- 격리 Python 3.11 환경: `.tools/notion-stt/venv/`.
- 의존성: `code_assist_v1/requirements-notion-local-stt.txt`.
  faster-whisper 1.2.1, CTranslate2 4.8.2, Flask 3.1.2, waitress 3.0.2.
  기존 공용 `venv/`에는 패키지를 추가하거나 교체하지 않았다.
- 모델: `.tools/notion-stt/models/base/`, 공개 `Systran/faster-whisper-base`.
  고정 revision: `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`.
  config.json/model.bin/tokenizer.json/vocabulary.txt 4개만 명시적으로 다운로드했다.
- model.bin SHA-256:
  `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9`.
- 추론: CPU int8, 최대 4 CPU threads, worker 1개. GPU/CUDA 설치 변경 없음.
  모델 다운로드 후 추론은 `local_files_only=True` 및 HF offline 모드다.

설치가 이미 완료된 이 PC에서 다시 실행할 때:

```powershell
cd G:\AI_Agent\DEMOS_CODEX
.\venv\Scripts\python.exe code_assist_v1/scripts/run_notion_local_stt_test.py
```

10322/10323이 사용 중이면 실행기는 자동으로 종료/덮어쓰지 않고 중단한다. 점유
프로세스를 먼저 확인해야 한다. 실행기는 자체 자식 STT/Notion 프로세스만 정리하며
10011 로그인/SBE 프로세스는 시작하거나 종료하지 않는다. 이를 종료하고 기존
`python -m code_assist_v1.app_notion --port 10322 --codex` 실행으로 돌아가면 로컬
테스트 설정이 남지 않는다. 전역 환경변수나 계정 인증은 변경하지 않는다.

## 연결·개인정보·한도

- 실행기가 매번 임의의 내부 인증 토큰을 메모리에서 만들고 자식 프로세스 환경으로
  전달한다. 토큰을 디스크·명령행·로그·브라우저에 기록하지 않는다. 외부 API 키가 아니다.
- STT 서비스는 loopback만 listen한다. 토큰 누락은 401, 외부 주소/Origin 포함 요청은
  403. CORS는 열지 않는다. Notion 서버의 편집 권한/동의 검사는 그대로 적용된다.
- 로컬 연결에서 OS HTTP proxy를 사용하지 않는다. 준비 상태/모델/인증을 확인하지
  못하면 Notion capabilities는 녹음 버튼을 활성화하지 않는다.
- 오디오는 영구 저장하지 않는다. 대용량 multipart의 요청 임시 파일은 요청 종료 시
  닫히고, 추론용 음성은 메모리에서 처리된다. 사용자 요청이 없는 녹음 백업은 없다.
- 파일당 20 MiB 및 **디코딩 후 120초** 제한. 계속 녹음은 12초씩 나누므로 회의 전체를
  120초로 제한하는 것은 아니다. 기존 80,000자 원문/10개 대기 구간 제한은 유지한다.
- 추론 최대 65초. 시간 초과/worker 종료 시 실패하고 해당 worker를 정리한다.
  이 경우 실행기를 재시작해야 한다. 임의의 다른 provider로 음성을 보내지 않는다.
- 프런트엔드 취소/권한 변경 시 결과는 기존 guard/큐 계약으로 폐기한다. 이미 로컬
  worker에 전달된 계산은 위 한도 안에서 마칠 수 있으나 별도 문서 쓰기는 하지 않는다.

## 직접 테스트 순서

1. 현재 문서 저장을 확인한 뒤 10322 페이지를 새로고침한다.
2. AI 노트에서 `로컬 Whisper base · CPU · 테스트` 표시를 확인한다.
3. YouTube Music을 **같은 Chrome 브라우저의 다른 탭**에 열면 해당 탭만 선택하기 쉽다.
   Codex 내장 브라우저의 선택 창에서 외부 Chrome 탭이 보이지 않으면 Notion도
   Chrome에서 `http://127.0.0.1:10322`로 연다. 필요한 경우 10011 로그인으로 연결한다.
4. 소리 입력은 `시스템·탭 오디오`, 언어는 노래/발화에 맞게 선택한다. 동의 확인 후
   `소리 공유로 회의 시작` → 공유 창에서 음악 탭 → `탭 오디오 공유`를 켠다.
   브라우저/OS가 실제로 오디오 track을 주지 않으면 오류를 표시하고 수집을 해제한다.
5. 20~30초 후 `회의 종료`. 원문 탭에서 인식 결과를 본다. 첫 결과는 12초 구간 수집과
   전사 시간이 지난 뒤 나타난다. 자동 요약은 첫 테스트에서 OFF로 유지한다.
6. 음악은 수집 확인용이며 가사 정확도·회의 요약 품질 기준이 아니다. 한국어 말하기,
   반주, 잡음, 무음은 별도 비교가 필요하다. 정확도 확인에는 직접 읽은 짧은 문장을 쓴다.
7. 전사 원문 확인 후 필요하면 `요약 생성`. 이 시점에 텍스트가 선택한 AI 모델로 전송된다.

## 이 작업의 검증

- 모델 실제 추론 + loopback HTTP: 7.858초 영어 합성 음성에서 의도한 문장 인식.
  두 실행의 전사 시간은 1.204초/1.875초. 전체 회의나 한국어/음악의 성능 보장은 아니다.
- 무음 3초 → 빈 전사; 121초 → 400/audio_too_long; 잘못된 파일 → 400/invalid_audio;
  인증 없는 health → 401; 인증된 준비 상태 → 200. 실제 사용자 음성은 전송하지 않았다.
- 서버/권한/라우팅/계약: 41 + 39 = 80 passed.
- Node: AI notes core, Meeting audio WAV/worklet/lifecycle/retry/backpressure 통과.
- 브라우저: 로컬 전사 후 자동 요약 0회/명시적 클릭 후 요약 1회, 기존 파일·마이크·
  시스템/혼합 수집·취소·트랙 해제·저장 회귀 **14 passed / 164.93초**.
  로컬 전사 UI 스크린샷을 열어 표시와 원문 누적을 확인했다.
- 합성 스크립트 `.tmp/smoke_notion_local_stt_20260911.py`, 샘플과 스크린샷
  `.tmp/notion-local-stt-20260911/`. 테스트 DB/사용자/페이지는 모두 격리 fixture다.
- `AGENTS.md` SHA-256 유지:
  `629fe4f841b6aa3ba4ea7b3dd987540e5a3bbcc0bcaf06c2d15f5367e881b763`.
  SBE hot contract/최종 Z0 gate/기존 immutable receipt는 변경하지 않았다.

근거: [faster-whisper 공식 저장소](https://github.com/SYSTRAN/faster-whisper),
[배포 모델](https://huggingface.co/Systran/faster-whisper-base/tree/main).

## 적용 결과 (2026-09-11 18:45 KST 무렵)

- 기존 10322의 정확한 실행 명령을 재검증하고 PID 12260만 종료한 뒤 실행기를 가동했다.
- Notion을 시작하기 전에 동일한 연결 환경에서 capabilities의 enabled/local_only를
  실제 로컬 서비스 health로 검증했다. 인증 토큰은 출력하지 않았다.
- 10322 새 listener PID 29296: /healthz status=ok, standalone. 공통 health의 회사/
  GGUF/Codex 순서는 AI 노트 STT의 local_test 모드와 다른 범위이므로 혼동하지 않는다.
- 10323 listener PID 37884: **127.0.0.1에만 bind**, 익명 /healthz는 401.
- 10011 listener PID 30092 유지, /login HTTP 200. 로그인 서버 재시작/변경 없음.
- 실행기는 이 작업의 exec session 37777에서 계속 실행 중이다. 실제 사용자의 페이지
  수정, 마이크/시스템 녹음, YouTube Music 재생 또는 가사 수집은 시작하지 않았다.
- 실제 한국어·음악·사용자 기기의 공유 picker/오디오 track 검증은 다음 사용자 테스트다.
  전체 제품/SBE/Z0 goal 완료를 선언하는 증거가 아니다.
