# SBE-8.3 natural-language Wiki overview repair evidence

Date: 2026-08-25 Asia/Seoul

Status: LOCAL_HTTP_AND_CODEX_PASS

## 1. User-path reproduction

The newest real admin turn was read from the retained canonical database rather
than inferred from a diagnostic fixture:

- conversation: `5b7724e2-77e6-572b-a5ce-661a03ece7db`;
- selected space: Part `AMHS설계파트`
  (`36ea8b84-5476-56d0-9552-dc877ac86518`);
- exact user text: `현재 위키 설명해줘`;
- executor: `model_id=codex-local`, `model_kind=codex`;
- observed answer: no Wiki reference material was available.

This proves that scope routing was correct in the reported attempt. The failure
was retrieval intent classification: SBE-8.2 recognized explicit knowledge-list
phrases but not the natural Wiki-overview phrasing above, and ordinary FTS had no
lexical document match.

## 2. Repair boundary

The deterministic inventory-intent recognizer now includes bounded overview
phrases such as `현재 위키 설명`, `이 위키 요약`, and their English equivalents.
It deliberately does not treat the bare token `위키` as inventory intent, so a
specific query such as `현재 위키에서 OHT 장애 설명` can still use ordinary
content retrieval rather than dumping the whole inventory.

## 3. Live proof after restart

10211 was restarted with only its verified launcher/listener processes replaced.
Ports 10011, 10111, and 10121 were not touched.

- listener PID: 45012;
- health: `ready`;
- `company_api_called=false`;
- marker SHA-256:
  `83c35d85ab9cdaba4359c33d9ccb2fa915c22d134220f99eb7a7327e70200ee1`.

The exact user text returned ten bounded Part candidates through authenticated
HTTP. A fresh real conversation then streamed to completion:

- conversation: `0f8d6358-cbcf-5088-9343-60bc1de45fd4`;
- UI title: `diagnostic-part-current-wiki`;
- selected space: `AMHS설계파트`;
- response: summarized the Part's sensor-mismatch return threshold,
  shift-handover rule, incident timeline, recovery evidence, and Team sharing;
- provider: Codex Local Bridge only.

## 4. Regression proof

- focused hybrid/canonical retrieval: 42 passed in 58.55 seconds;
- complete relevant preview/browser/retrieval/Agent set: 80 passed in 99.67
  seconds;
- Python compilation and JavaScript syntax checks: pass.

Changed implementation SHA-256:

- `knowledge_core/repositories/hybrid_retrieval.py`:
  `ed59e55ff336435a2391c0591b94559f96e995620dd3f3e1cdb6ab1ce11a08a4`.
