# Manual reference intake foundation — 2026-08-20

Status: implementation and contract tests green; no Notion reference was
observed or promoted.

## Outcome

An import-side-effect-free manual reference intake now provides:

- deterministic worksheets derived only from the verified 25-scenario plan;
- an identifier-free C0 CLI that fails unless the operator confirms the
  visible signed-in Chrome and both synthetic workspace aliases;
- deliberately incomplete submission drafts after a matching passing C0;
- exact ordered coverage of every `capture_after` action;
- capture/action/source-build/redaction/operator-attestation validation; and
- exclusive canonical JSON output restricted to a new file below the trusted
  OS temporary root, with repository, existing-file, link, and reparse-point
  refusal; and
- an identifier-free receipt whose `promotion_allowed` is always false.

The verifier refuses a submission before reading its file unless
`--synthetic-only-confirmed` is explicit. It rejects C0 drift, action drift,
missing or reordered captures, mixed source builds, unsafe strings, personal
access, raw DOM/network/cookie/storage capture, public posting, and external
side effects.

## Evidence

- focused syntax and intake suite: `17 passed in 9.20s`;
- intake plus C0/action/capture/redaction/packet/plan/completion bundle:
  `82 passed in 26.78s`;
- no production UI, route, schema, auth, Knowledge Core, or Second Brain source
  changed.

The exact operating procedure is
`CORE_FREE_WEB_UI_MANUAL_REFERENCE_INTAKE_RUNBOOK.md`.

## Current external gate

The Chrome control path still fails during host initialization before browser
or tab selection. The failure occurred before any Notion workspace, label, or
page was read. This manual path permits the user to make an explicit synthetic
observation without weakening the evidence contract, but it does not fabricate
the observation and does not remove the need for user participation or a
repaired browser transport.
