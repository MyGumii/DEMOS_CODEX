# 로컬 음성 수집을 고정 12초로 통일

> **대체됨:** 아래는 사용자 요청을 잘못 해석한 직전 구현 기록이다.
> 사용자는 말끝 우선과 고정 12초를 남기고 고정 10초만 제거하도록 정정했다.
> 현재 동작은 `NOTION_SPEECH_FIXED12_OPTIONS_20260913.md`를 따른다.

2026-09-13. 사용자 후속 결정: 12초 간격을 남기고 10초를 없앤다.
이 문서는 `NOTION_SPEECH_BOUNDARY_COMMENTS_FIX_20260913.md` 이후의
음성 간격 설정 변경을 기록한다. 이전 검증 기록은 역사적 증거로 유지한다.

## 변경

- 로컬 Whisper 음성 처리 간격 선택에는 `고정 · 12초`만 남는다.
  10초 고정 및 말끝 우선 10~20초를 선택하거나 기본값으로 실행하지 않는다.
- 예전 노트에 저장된 10초, 2/4/8초 또는 speech 설정도 새 녹음에서는
  fixed/12초로 해석한다. 노트를 읽기만 해서는 저장된 속성이나 본문을 바꾸지 않는다.
- 사내 등 non-local STT provider는 서버의 기존 chunk_seconds를 사용한다.
- 번역 안내도 12초 수집으로 맞췄다. 전사/번역 응답 시간은 별도로 추가된다.
- 회의 종료·일시정지의 짧은 마지막 음성 처리, 이번 녹음 범위 격리,
  원문·번역 보존, 기존 모델/권한/비공개 비교 계약은 유지한다.
- AudioWorklet의 기존 speech 구현은 삭제하지 않았지만, 현재 UI/녹음 경로는
  fixed만 전달한다. 이번 변경은 서버나 STT provider의 재설정이 아니다.
- 관련 두 script URL을 `20260913-fixed12-only`로 갱신했다.

## 변경 파일

- `notion_static/notion_ai_notes.js`
- `notion_static/notion_notes_translation.js`
- `notion_static/index.html`
- 기존 `tests/test_notion_ai_notes.js`의 설정 기대값
- 기존 `tests/browser/test_notion_speech_boundary.py`를 현재 간격 정책과
  legacy 설정 호환 검증으로 갱신
- 기존 `tests/browser/test_notion_notes_translation.py`의 선택값 기대값

## 검증

- 두 JavaScript 문법 검사 통과.
- Node AI notes, meeting audio, notes translation 테스트 통과.
- 격리 브라우저 **5 passed, 142.24초**. 합성 음성만 사용했다.
  실제 업로드 WAV 12초, 이전 10초/speech 설정 호환, 원문·번역 보존,
  새로고침 후 자동 번역 OFF, 이전 녹음 대기열 제외와 지연 표시를 확인했다.
- `fixed12-only-picker.png` 합성 화면을 직접 열어 고정 12초 표시와 배치를 확인했다.
- AGENTS SHA-256 유지:
  `629fe4f841b6aa3ba4ea7b3dd987540e5a3bbcc0bcaf06c2d15f5367e881b763`.

## 적용

정적 frontend 변경이므로 서버 재시작은 필요하지 않다. 이미 열려 있는 사용자
탭은 녹음·전사·저장 완료 후 새로고침해야 한다. 이번 작업에서 실제 사용자 탭을
새로고침하거나 녹음을 종료하지 않았으며, 사용자 원문·번역·댓글을 수정하지 않았다.
전체 Internal Collaborative Notion/SBE/Z0 완료 상태는 변경하지 않았다.
