# Notion Codex 직접 선택 및 실행 인자별 숨김

2026-09-12 사용자 요청: `--codex`로 실행하면 Codex도 직접 선택할 수 있게 하고,
옵션이 없으면 비활성 항목을 남기지 말고 화면에서 제외한다.

## 현재 정책

- 번역 및 AI 노트 요약 모델 선택기에 동일하게 적용한다.
- `--codex`와 기존 CLI 연결 확인을 통과하면 `Codex Local Agent`를 선택할 수 있다.
- Codex를 직접 선택한 요청은 `model_id=codex-local`로 전달한다. 번역 서버는
  그 모델만 호출하며, 직접 선택 실패를 다른 회사 모델 호출로 바꾸지 않는다.
- 빈 ID인 **자동** 선택은 Gemma4 → Qwen3.6 → 연결된 Codex 순서를 유지한다.
- 옵션이 없거나 연결이 확인되지 않으면 Codex 옵션과 전용 안내를 숨긴다.
  저장된 과거 Codex ID도 사용할 수 없는 후보로 다시 노출하지 않는다.
- 번역은 호출 직전 연결을 다시 확인하며 기존 권한·잠금·읽기 전용 임시 작업공간·
  ephemeral 호출 계약을 유지한다. 요약 및 비공개 비교의 서버 선택 검증도 유지한다.
- 음성 인식은 기존 Whisper/STT가 담당한다. Codex 직접 선택은 전사된 텍스트의
  번역·요약에 대한 선택이며 Codex 음성 인식기를 새로 추가한 것이 아니다.
- 사용자 페이지의 원문/모델 설정을 직접 수정하거나 실제 녹음을 시작하지 않는다.

이 변경은 `NOTION_TRANSLATION_ACCESS_AND_FALLBACK_AUDIT_20260911.md`의
"Codex는 직접 선택 불가, fallback 전용" 부분만 후속 사용자 요청으로 대체한다.
그 문서의 과거 감사·cleanup 기록은 그대로 보존한다.

## 구현 범위

- `notion_notes_translation.py`: 명시적 Codex ID를 직접 선택으로 처리.
- `notion_static/notion_notes_translation.js`: selectable 옵션, 선택값/요청 유지,
  직접 선택과 자동 선택의 차이를 안내.
- `notion_static/notion_ai_notes.js`: 요약 및 비공개 비교 요청에서도 선택값 유지.
- `notion_static/index.html`: 두 스크립트 캐시 키 `20260912-select1`.
- 관련 단위/브라우저 테스트: ON/OFF, 회사 모델 유무, 선택·저장·재접속·요청 ID,
  연결 해제 및 기존 자동 fallback 검증.
- AGENTS.md, SBE hot contract, 공유 model_routing/internal_llm/codex_bridge,
  notion.js 및 사용자 DB는 변경하지 않았다.

## 검증 및 실행

- 서버 회귀: **77 passed / 65.08초**.
- Node: 번역 선택/구간/저장 실패/취소 및 AI notes core 통과. 두 JS 구문 검사 통과.
- 격리 브라우저 회귀: **11 passed / 184.94초**. 직접 선택, 저장·재접속 후 유지,
  요청의 모델 ID, 옵션 미지정 시 완전 숨김 및 기존 번역·요약·비교 동작을 확인했다.
- 테스트는 OS Temp 격리 fixture와 합성 텍스트/오디오만 사용하며 실제 모델을 호출하지
  않는다. 이번 실행은 workspace 스크린샷 디렉터리를 지정하지 않았다.
- 사용자 응답 `진행 중 아님 — 재시작 가능` 확인 후 기존 Notion/STT 실행기 트리를
  점검하고 같은 `--comparisons --codex` 인자로 재시작했다. 10011은 유지했다.
- 재시작 후 Notion 10322(PID 25904), loopback STT 10323(PID 38524),
  실행기 PID 18604를 확인했다. Notion `/healthz`는 `status=ok`이며 기존
  `company_api, local_gguf, codex` 공급자 설정을 유지한다.
- 익명 정적 파일 요청은 로그인으로 리디렉션된다. 따라서 이 요청의 HTTP 200을
  인증된 최신 JS 제공의 증거로 취급하지 않았다. 기능 검증은 위 격리 브라우저
  테스트로 수행했으며 실제 사용자 페이지는 새로고침 후 확인할 수 있다.
- 이번 테스트의 OS Temp 루트 `code-assist-pytest-t28fxcyx`,
  `code-assist-pytest-sl9ous7a`가 종료 후 남아 있지 않은 것을 확인했다.
- 이번 서버 운영 로그: `.tmp/notion-web-select-20260912/`.
