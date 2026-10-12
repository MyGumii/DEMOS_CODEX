# 개인화 / 메뉴 · 시스템 오디오 회의 노트 v3

2026-09-11. 이 문서는 추가 구현 기록이다. 이전 v1/v2, Notion reference inventory,
immutable receipt 및 SBE hot contract를 대체하지 않는다.

10011 app_code 연결 조사와 사내 실행 절차는
`NOTION_AI_NOTES_COMPANY_AUDIO_HANDOFF_20260911.md`에 별도로 기록했다.
사용자 지시에 따라 미확인 음성 provider 설정은 활성화하지 않는다.

## 구현 범위

- `/` 메뉴 위에서 `최근 사용` 또는 `최근 30일 사용 빈도`를 선택한다.
  집계는 최근 30일 중 최대 300회의 명시적 명령 선택을 사용한다. 파일 선택기를 여는
  명령도 사용으로 센다. 검색·호버·고정 클릭은 집계하지 않는다.
- 각 항목의 별표로 최대 8개를 고정/해제한다. 고정 섹션이 추천보다 위에 표시되며,
  나머지 기본/미디어/DB/고급/임베드 reference 순서는 유지된다.
- 개인화 정보는 사용자 ID와 워크스페이스 ID별로 현재 origin의 localStorage에
  저장한다. 명령 type ID·시각만 저장하며 문서 내용은 저장하지 않는다. 다른 기기,
  브라우저, localhost/127.0.0.1 사이에는 동기화되지 않는다. 권한 필터를 통과한
  명령만 추천/고정에 나타난다. 저장소 차단·손상 시 안전한 기본값/오류 안내를 쓴다.
- AI 노트 소리 입력: 시스템·탭(기본), 시스템·탭 + 마이크, 마이크만.
  Zoom/Webex 전용 API나 봇이 아니라 사용자가 선택한 소리를 수집한다.
- 언어 자동 감지 또는 한국어/영어/일본어/중국어 입력 언어를 전달한다.
- 번역·요약 언어를 선택하면 원문을 유지하고 요약·결정·할 일을 해당 언어로 작성한다.
  근거 인용·담당·기한은 원문 그대로 검증한다. 원문 전체 번역 기능은 아니다.
- 12초 단위 PCM16 WAV 전사, 첫 원문 저장 후 잠정 요약, 이후 변경 시 30초 간격
  요약 점검, 종료 후 최종 요약을 유지한다. 단어 단위 스트리밍이나 완료 지연 보장은 아니다.

## 소리 공유와 개인정보

녹음·전사 동의를 확인하고 사용자가 시작을 눌러야 브라우저 선택기를 연다.
getDisplayMedia는 시작 클릭의 사용자 활성화 안에서 호출하며 저장/네트워크 await를
앞에 두지 않는다. 매번 선택기 허용이 필요하고, 지원되는 소리 범위는 브라우저·OS·
선택 대상에 따라 다르다. 오디오 트랙이 없는 공유는 거부하고 모든 트랙을 닫는다.
이 동작은 [MDN getDisplayMedia 계약](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)에 따른다.

시스템 전체 공유에는 다른 앱·알림 소리도 들어갈 수 있다. 화면 영상 트랙은 브라우저
선택기 계약상 필요하지만 앱은 영상 프레임을 읽거나 인코딩·전송·저장하지 않는다.
AudioWorklet은 소리를 다시 재생하지 않는다. 혼합 모드는 각 입력에 0.5 gain을 사용한다.
취소, 마이크 거절, 공유 종료, 오류, 페이지 권한 변경, 페이지 이탈 시 트랙·연결·
AudioContext를 해제한다. 권한이 사라진 결과는 페이지에 반영하지 않는다.

대기 구간은 브라우저 메모리에만 남는다. 실패한 전사/저장을 재시도하거나 오디오를
명시적으로 내려받을 수 있다. 영구 오디오 보관은 이 앱 서버에서 하지 않는다.
외부 제공자의 보관 정책은 별도 확인 대상이다. 탭/브라우저 종료 후 대기 음성 복구는
보장하지 않는다. 사용자의 실제 페이지나 실제 마이크/시스템 소리는 테스트하지 않았다.

## 모델 경로와 현재 미설정 부분

AI 노트 텍스트 요약/번역의 자동 경로는 회사 API Gemma4 → Qwen3.6 → 연결된 Codex다.
각 계열 첫 활성 모델 하나씩, 최대 3회이며 이 경로에는 GGUF를 넣지 않는다.
사용자가 특정 모델을 직접 선택하면 그 선택만 시도한다. 공유 `internal_llm.py`,
`models.py`, `model_routing.py`, `codex_bridge.py`는 수정하지 않았다.

Codex 연결 확인은 기존 launch flag와 CLI의 조용한 `login status` 성공을 모두 요구한다.
기존 bridge도 실행 시 인증을 다시 확인한다. 연결을 자동 로그인시키거나 인증을
복사·노출하지 않는다.

**현재 등록된 Gemma4/Qwen3.6에는 meeting_audio 설정이 없다. 현재 Codex CLI bridge는
텍스트 입력용이다. 실제 STT와 Codex 음성 fallback 완료를 주장하지 않는다.**
음성 지원이 확인된 endpoint·프로토콜을 관리자가 설정해야 오디오 시작을 활성화한다.
회사 모델 이름이나 텍스트 채팅 연결만으로 음성 지원을 추정하지 않는다.
OpenAI Docs 스킬과 로컬 bridge 코드 확인을 통해 텍스트 경로와 오디오 경로를 분리했다.

STT 전송 프로토콜은 다음 두 개다.

| 프로토콜 | 요청 | 응답 |
| --- | --- | --- |
| `multipart` | file, model, response_format=json, 선택적 language | `{"text":"전사 원문"}` |
| `chat_audio` | model, messages의 input_audio base64, WAV/MP3, stream=false, store=false | choices[0].message.content 문자열 |

`chat_audio`는 [공개된 multimodal chat 입력 형식](https://developers.openai.com/api/docs/guides/audio-chat-completions)에 호환되는
배포용 어댑터다. 이것이 Hynix Gemma4/Qwen3.6 배포의 지원 증거인 것은 아니다.
회사 endpoint가 다른 스키마이면 그 공식 계약에 맞춘 어댑터가 추가로 필요하다.

### 회사 API 설정

실제 설정값/비밀은 이 문서나 브라우저에 넣지 않는다. 다음 환경변수를 **10322 서버
프로세스를 시작하는 환경**에 설정하고 재시작한다. 두 API 모두 설정할 수 있다.

| 우선순위 | 환경변수 prefix |
| --- | --- |
| 1 Gemma4 | `CODE_ASSIST_NOTION_STT_GEMMA4_` |
| 2 Qwen3.6 | `CODE_ASSIST_NOTION_STT_QWEN36_` |
| 3 선택적 오디오 지원 로컬 bridge | `CODE_ASSIST_NOTION_STT_CODEX_` |

각 prefix 뒤에 `URL`, `MODEL`, `PROTOCOL`, 선택적으로 `API_KEY_ENV`, `API_KEY`,
`CA_BUNDLE`을 붙인다. URL은 전체 POST 주소이며 경로를 추측해서 추가하지 않는다.
명시한 키 환경변수가 비면 다른 키로 대체하지 않는다. URL 사용자명/비밀번호,
query/fragment, redirect를 거부하고 TLS 검증을 끄지 않는다. 원격에는 HTTPS를 권장한다.

기존 배포 소유 모델 registry 안에 `meeting_audio` 객체를 넣는 방식도 지원한다.
예시 스키마(placeholder이며 실제 등록 파일을 이 작업에서 수정하지 않음):

```json
{
  "meeting_audio": {
    "url": "https://approved-company-audio.example/actual-endpoint",
    "model": "DEPLOYED_AUDIO_CAPABLE_MODEL_ID",
    "protocol": "chat_audio",
    "api_key_env": "APPROVED_COMPANY_AUDIO_KEY"
  }
}
```

환경변수가 우선한다. MODEL을 생략하면 해당 회사 registry의 모델 ID를 사용한다.
음성 전용 키가 없으면 해당 모델의 기존 서버 인증 계약을 재사용한다. 모델의 텍스트
URL을 음성 endpoint로 자동 복사하지 않는다.
기존 `CODE_ASSIST_NOTION_STT_URL/MODEL`은 새 회사 음성 설정이 전혀 없을 때만
호환 경로로 유지한다. 새 회사 경로 중간에 임의의 네 번째 제공자를 끼우지 않는다.

### Codex 음성 fallback 경계

`CODEX_URL`은 **별도로 구현·배포된 오디오 지원 로컬 gateway**가 있을 때 연결할 수
있도록 준비한 지점이다. 이 작업은 그러한 gateway 또는 Codex STT 엔진을 만들지 않았다.
현재 텍스트 bridge에 오디오를 문자열로 밀어 넣지 않는다. URL은 loopback만 허용하고,
`--codex` 활성화 + CLI 인증 확인 + 명시적 음성 endpoint 설정이 모두 필요하다.
설정/연결이 없는 경우 호출하지 않는다. 첫 두 경로 실패 후 호출 직전에도 연결을
재확인한다. 따라서 텍스트 Codex 연결만 있는 지금은 STT fallback으로 쓸 수 없다.

## 검증

- Node 5개 suite: reference order, 개인화 격리·빈도·최근·고정 한도, editor/runtime,
  AI notes, WAV/worklet/lifecycle/retry/backpressure 통과.
- Python API/권한/경로/계약 회귀 70개 통과. 마지막 provider/오디오 보강 후 해당
  53개 재실행도 통과. 합성 HTTP multipart, chat_audio wire payload, Gemma→Qwen→
  연결된 bridge fallback, disconnect skip, revocation fail-closed, 번역 지침 포함.
- 새 브라우저 개인화/시스템 오디오 5개 통과. 별표 클릭 후 메뉴 닫힘 버그를 수정하고,
  종료 검증이 이전 잠정 요약 상태가 아니라 실제 트랙 종료·최종 상태를 기다리도록 교정했다.
- 최종 브라우저 회귀 **19 passed / 209.80초**: 개인화 1, 시스템/혼합 캡처 2,
  무음 트랙·취소 2, 공유 종료 1, 혼합 마이크 거절 1, 기존 음성 6, slash/수식/HTML 4,
  AI 노트 2. 실제 브라우저 AudioWorklet + 합성 oscillator를 사용했다.
- 이미지 검토: `.tmp/notion-personal-system-audio-20260911/personal-slash-pins.png`,
  `system-audio-system.png`, `system-audio-mixed.png`.

## 로컬 반영

10322 개발 서버를 `python -u -m code_assist_v1.app_notion --port 10322 --codex`로
재시작했다(PID 12260). `/healthz` 200/ok. 요청된 Codex fallback launch 옵션만
활성화했으며 default 모델/registry/SBE gate는 변경하지 않았다.
기존 10011 로그인 서버는 PID 30092 그대로 유지, `/login` 200.
localhost/127.0.0.1의 기존 10011/10322 origin 허용 범위를 유지했다.

서버 설정 읽기 전용 검사에서 AI 노트 후보는 Gemma4-31B, Qwen3.6-35B-A3B,
인증된 codex-local 순으로 확인했다. 음성 capabilities는 설정 미비로 disabled.
실제 제공자 inference/음질/전사 정확도/번역 품질은 검증하지 않았다.
익명 HTTP의 static/index 요청은 로그인으로 이동하므로, 익명 200을 인증된
프런트엔드 확인으로 취급하지 않았다. 새 자산 로딩과 UI는 격리 로그인 브라우저에서
검증했다. 실제 사용자 페이지는 저장 완료 후 새로고침해야 새 JS가 적용된다.

AGENTS.md SHA-256 유지:
`629fe4f841b6aa3ba4ea7b3dd987540e5a3bbcc0bcaf06c2d15f5367e881b763`.
실데이터 테스트, migration, 영구 삭제, 계정/공유 권한 수정은 없다. 제품/SBE Z0 goal을
완료로 변경하지 않았다.
