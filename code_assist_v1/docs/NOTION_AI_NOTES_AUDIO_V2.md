# AI 노트 음성 입력·회의 중 요약 v2

2026-09-11. 텍스트 v1 위에 추가한 기능이다. v1 검증 기록을 대체하지 않는다.

## 사용 흐름

1. `/회의` 또는 `/AI`로 AI 노트를 삽입한다.
2. 참석자에게 녹음과 설정된 API로의 전송을 알리고, 동의를 확인한 뒤 체크한다.
3. **마이크로 회의 시작**을 누른다. 브라우저 마이크 권한이 필요하다.
4. 원문에 시간 위치와 전사 텍스트가 누적된다. **메모** 탭에서 직접 작성도 가능하다.
5. 첫 구간 저장 후 잠정 요약을 만들고, 이후 변경분이 있으면 30초 간격으로 갱신한다.
   동시에 여러 요약 요청을 보내지 않고 대기 요청을 합친다. 자동 갱신은 끌 수 있다.
6. **일시정지**는 새 음성 수집을 멈춘다. 이미 수집한 구간의 전사는 계속된다.
7. **회의 종료**는 마지막 짧은 음성 구간까지 전사·저장한 뒤 최종 요약을 생성한다.
   최종 요약 실패 시 원문은 유지되며 수동 재생성이 가능하다.

마이크 대신 **음성 파일 가져오기**를 사용해도 된다. WAV/MP3/M4A/WebM/OGG/FLAC,
최대 20 MiB이며 실제 코덱 지원은 연결한 전사 서비스에 따른다. 파일은 최종 요약만
생성한다. 텍스트 붙여넣기/텍스트 파일 가져오기는 STT 없이도 계속 사용할 수 있다.

이 구현은 단어 단위 스트리밍이 아닌 **구간 단위 준실시간**이다.
마이크 PCM을 약 12초씩 독립적인 mono PCM16 WAV로 전송한다. 전사 API 처리 시간과
요약 모델 처리 시간이 추가된다. 30초는 요청 점검 간격이지 완료 시간 보장이 아니다.
전사 중 원문은 자동 누적용 읽기 전용이며, 회의 종료 후 직접 교정할 수 있다.

## 음성 API 연결

서버의 기존 Gemma4/Qwen3.6 모델 설정은 **텍스트 요약**에 사용한다.
두 모델이 음성을 직접 인식한다고 가정하지 않는다. 전사는 다음의 별도 환경변수로
명시적으로 설정한 multipart 호환 서비스만 호출한다.

| 환경변수 | 값 |
| --- | --- |
| `CODE_ASSIST_NOTION_STT_URL` | 실제 전사 POST endpoint 전체 주소. `/v1/audio/transcriptions` 호환 예시이며 경로를 자동 추가하지 않음 |
| `CODE_ASSIST_NOTION_STT_MODEL` | 그 endpoint에 실제 배포된 음성 인식 모델 ID |
| `CODE_ASSIST_NOTION_STT_API_KEY_ENV` | 선택. 전사 API 토큰이 들어 있는 환경변수 이름 |
| `CODE_ASSIST_NOTION_STT_API_KEY` | 선택. 위 이름을 지정하지 않을 때 사용할 전사 API 전용 토큰 |
| `CODE_ASSIST_NOTION_STT_CA_BUNDLE` | 선택. 사내 TLS 인증서 검증용 CA bundle 경로 |

이 값들을 **Notion 서버 프로세스(10322)를 시작하는 환경**에 설정하고 재시작한다.
기존 로그인 서버(10011)에는 STT 값을 넣을 필요가 없다. API 비밀값을 채팅/문서나
클라이언트 코드에 넣지 않는다. 별도의 키 환경변수 이름을 지정했는데 값이 비면
다른 토큰을 대신 사용하지 않는다. 인증서 검증을 끄지 않는다. 원격 연결은 HTTPS를
사용한다. URL 내 사용자명/비밀번호/쿼리/fragment, 리다이렉트는 허용하지 않는다.

프로토콜은 `multipart/form-data`의 `file`, `model`, `response_format=json`,
`language=ko`이며 응답은 `{"text":"전사 내용"}`이다. 필요하면
`Authorization: Bearer ...`를 서버에서만 추가한다. 회사 API가 다른 계약이면
그 계약에 맞는 adapter 교정이 필요하다. **URL/model 설정 확인은 API 접속 성공이나
음성 인식 품질 검증을 의미하지 않는다.** 실환경 endpoint는 아직 지정·검증하지 않았다.

## 안전·저장·실패 처리

- 사용자가 녹음 시작을 누르기 전에는 마이크를 요청하지 않는다. HTTPS 또는 localhost 필요.
- 마이크 입력만 처리한다. 시스템/회의 앱 오디오 자동 캡처, 화자 자동 식별,
  참석자 자동 초대는 구현하지 않는다. 헤드폰으로 듣는 상대 음성은 마이크에 안 들어올 수 있다.
- 원본 오디오는 이 앱에서 영구 저장하지 않는다. 업로드 파서의 임시 spool은 요청 종료 시
  정리된다. 외부 전사 제공자의 보관 정책은 별도로 확인해야 한다.
- 대기 음성은 브라우저 메모리에 남는다. 전사/저장 오류 시 녹음을 중단하고 재시도와
  대기 음성 다운로드를 제공한다. 저장만 실패하면 이미 받은 전사 응답을 재사용해
  텍스트가 중복되지 않도록 한다. 전사 지연으로 대기가 10구간에 이르면 녹음을 멈춘다.
- 페이지 이동·로그아웃에는 종료/저장 확인을 거친다. 실패한 대기 음성을 버리는 이동에는
  별도 확인을 한다. 새로고침/탭 닫기는 경고하지만 브라우저 종료 시 오디오 복구를 보장하지 않는다.
- 페이지 편집 권한 변경/블록 삭제 시 마이크와 클라이언트 요청을 중단한다.
  이미 서버/제공자에 전송한 요청 자체를 되돌릴 수는 없다. 서버는 전송 경계와 반환 전에
  권한을 다시 확인한다. 보기/댓글 권한, 잠금 페이지, 다른 workspace는 전사할 수 없다.
- 원문 80,000자, 메모 12,000자, 지침 2,000자. 한도에 도달하면 대기 음성을 내려받고
  새 AI 노트에서 이어간다. 긴 파일은 미리 나눠야 한다.
- 기존 저장/협업/공유 계약을 사용한다. 전사 API 자체는 페이지 DB에 쓰지 않는다.
  결과/원문/메모는 기존 블록 저장으로 저장되고 Markdown 검색/내보내기를 유지한다.
- 잠정 요약은 처리 중 원문이 뒤에 추가된 경우에만 적용한다. 기존 원문 교정 또는
  메모/지침 변경 시 오래된 응답은 거부한다. 최종 요청은 마지막 저장 원문 전체를 사용한다.
- 후반의 정정/취소를 반영하도록 지시하지만 실제 모델의 정확도는 별도 확인이 필요하다.
  결정/할 일에는 원문 인용을 검증하고 AI 초안임을 표시한다.

## 추가 API

`GET /api/notion/ai-notes/capabilities`에 `audio.enabled`, 입력 한도, 구간/요약 간격,
미설정 이유를 추가했다. 토큰/endpoint는 반환하지 않는다.

`POST /api/notion/ai-notes/transcribe`는 로그인과 편집 권한, multipart 파일 1개,
`page_id`, `workspace_id`(협업 모드), `segment_id`, `consent=true`, 선택 `language`를 받는다.
`status=transcribed`, 동일 `segment_id`, `text`, 서버 설정 `model`을 반환한다.
프로세스당 동시 전사 최대 2개. 파일 20 MiB, multipart 전체 20 MiB + 64 KiB,
응답 1 MiB/텍스트 80,000자 제한. 연결 타임아웃 10초, 읽기 타임아웃 90초이며
응답 수신 중 100초 초과도 거부한다. 한도 413/429, 인증 401/403/404,
입력 400, API 미설정/전사 실패 503. upstream 본문/비밀값은 오류에 노출하지 않는다.

`POST /api/notion/ai-notes/summarize`는 선택 `summary_phase=provisional|final`을 추가했다.
기존 요청의 기본값은 `final`이다. 기존 NP-1B legacy/bridge 59개 route registrar는 변경하지 않았다.

## 검증 범위

- `tests/test_notion_audio.py`: 합성 WAV, 실제 loopback multipart, 권한/회수/잠금/동의,
  요청·응답 한도, 재지정 방지, API 오류/비밀값 차단, DB 무변경.
- `tests/test_notion_meeting_audio.js`: WAV 헤더/PCM, 실제 worklet의 가변 입력 처리,
  pause/tail flush, 순차 전사, 중복 종료, 저장 재시도 중복 방지, 취소/권한 거부/대기 상한.
- `tests/browser/test_notion_audio.py`: 격리된 Chrome, 합성 oscillator 미디어 스트림과
  실제 AudioWorklet을 사용. 파일/동의/저장/새로고침, 잠정·최종 요약, 텍스트 메모 병행,
  일시정지/재개/종료, 실패 재시도, 권한 회수, 이동 취소/종료, 느린 요약의 최종 요청 병합.
- 기존 AI Notes, editor core/UI runtime, 수식/목차, routes/AI routing/NP-1B 계약 회귀 검증.

실제 사용자의 마이크, 실제 회사 모델, 실제 외부 전사 API는 테스트하지 않았다.

최종 로컬 결과(2026-09-11): Python API/회귀/계약 **64개 통과**(37.82초),
브라우저 **10개 통과**(110.35초), Node WAV/worklet/meeting lifecycle,
AI notes, editor-core, UI-runtime **4개 스크립트 통과**. JavaScript 구문 검사 통과.
느린 잠정 요약과 자동 저장 경합에서 대기 저장을 실패로 오인하던 문제도
AI 노트 adapter 내에서 교정했으며 공통 페이지 저장 계약은 수정하지 않았다.
협업 메인 편집기는 기존 암호화 outbox의 로컬 영속화를 기다린다.
합성 화면: `.tmp/notion-ai-notes-audio-20260911/ai-notes-audio-final.png`,
`ai-notes-live-transcript.png` 및 기존 수식/목차 회귀 화면.
AGENTS.md SHA-256은 `629fe4f841b6aa3ba4ea7b3dd987540e5a3bbcc0bcaf06c2d15f5367e881b763`로 변경 없다.

로컬 반영: 10322 Notion 개발 서버 재시작 후 `/healthz`가 `ok`, 10011 기존 로그인
서버는 재시작 없이 `/login` 200을 확인했다. 익명 전사 요청은 401이며 정적 앱 파일도
기존 인증 정책에 따라 익명 요청을 로그인으로 보낸다. STT URL/model 환경변수는
미설정이며 실제 음성 입력은 연결 설정 전까지 비활성이다. 운영 배포/Z0 완료 선언이 아니다.

## 프로토콜 참고

[Speech-to-text API](https://developers.openai.com/api/docs/guides/speech-to-text),
[AudioWorkletProcessor.process](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorkletProcessor/process),
[getUserMedia 보안·권한](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

OpenAI Docs 스킬을 사용해 multipart 전사 계약을 확인했다. OpenAI endpoint나 모델을
기본값으로 등록하거나 비용이 발생하는 호출을 실행하지 않았다.
