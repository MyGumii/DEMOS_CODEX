# Diffusion Concept Erasure 논문 정리 패키지

이 폴더는 Codex 세션에서 조사·작성한 다음 7편의 논문에 대한 초보자용 설명 자료입니다.

1. Erasing Undesirable Concepts in Diffusion Models with Adversarial Preservation
2. Erasing Concepts from Diffusion Models (ESD)
3. Erasing Concept Combinations from Text-to-Image Diffusion Model (CoGFD)
4. Mass Concept Erasure in Diffusion Models with Concept Hierarchy (SuPLoRA)
5. Receler: Reliable Concept Erasing of Text-to-Image Diffusion Models via Lightweight Erasers
6. Side Effects of Erasing Concepts from Diffusion Models (SEE)
7. R.A.C.E.: Robust Adversarial Concept Erasure for Secure Text-to-Image Diffusion Model

## 파일

- `index.html`: 핵심 결과물. 브라우저에서 바로 열 수 있는 독립형 인터랙티브 가이드입니다.
- `SESSION_SUMMARY.md`: 사용자의 요청, 조사 과정, 논문 간 연결성, 주요 결론을 기록한 세션 요약입니다.
- `codex-session/`: 현재 Codex 세션 원본을 찾을 수 있을 경우 보존하는 폴더입니다.

## 보는 순서

`index.html`을 열고 다음 순서로 읽는 것을 권장합니다.

1. 30초 요약과 확산 모델 배경
2. ESD 수식 실험실
3. 연구 계보 flowchart
4. 7편의 논문별 해설
5. 비교표와 용어집

## 핵심 관점

여기서 concept erasure는 특정 훈련 샘플의 물리적 삭제를 증명하는 작업이 아니라, 특정 조건에서 모델의 생성 행동을 바꾸는 model editing 문제로 다룹니다. 따라서 삭제 성공률뿐 아니라 우회 공격에 대한 강건성, 비표적 개념 보존, 조합·계층 확장성, 부작용을 함께 평가해야 합니다.

