# Core Free Web UI release-readiness runbook

Status: development evidence complete; release pending  
Profile: `free-owner-chrome-desktop-ko-KR`  
Live feature enablement: not authorized  
Live data access: none

## 1. What is complete

The adopted Core profile has 25 of 25 satisfied scenario evidence contracts.
That total consists of 20 reference differentials, three local-only Second
Brain integration contracts, one local failed-save recovery contract, and one
approved isolated-local performance contract. The evidence review gate has no
pending scenario summaries.

The H9 synthetic foundation is also green: it exercises explicit opt-in,
kill-switch denial, a quiesced copy-on-write backup, one canary publication,
and non-destructive restore into a new isolated root. This is development and
recovery evidence, not a live rollout.

## 2. What is not complete

Four release gates remain:

1. a formal human accessibility review, including screen-reader coverage;
2. legal and brand review of the shipped surface and product wording;
3. operational backup and restore on an explicitly approved deployment target;
4. an explicitly authorized live canary and rollback decision.

The default-off pilot-control foundation is now implemented and contract
tested. It requires exact enablement, an independent kill switch, nonempty
subject and workspace allowlists, a ready workspace substrate, and complete
allowlisting of the current actor's visible workspace inventory. Its readiness
response is authenticated, identifier-free, non-cacheable, and does no catalog
I/O when the configuration preflight is disabled or invalid. No live flag or
allowlist was enabled while implementing it.

These gates are intentionally outside the 25-scenario denominator. Passing all
scenario evidence must never silently approve a live feature flag, filesystem
restore, canary, external publication, invitation, or production migration.

## 3. Machine gate

Run from the repository root:

```powershell
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_core_free_ui_release_readiness --compact
```

A green result means the evidence and pending boundary are internally
consistent. Its expected release state is
`pending_external_and_operational_gates`; `release_ready` remains `false`.

The verifier binds the exact completion matrix, evidence-review gate,
performance checkpoint, isolated H9 checkpoint, navigation default-off
contract, Core UI pilot-control contract, and this runbook by SHA-256. It
rejects path escape, links, missing files, hash drift, duplicate or non-finite
JSON, premature completion claims, and self-promoted human approvals.

## 4. Order for closing the remaining gates

1. Freeze an accessibility review packet and have an identified human reviewer
   record the result; automated keyboard evidence is supporting material, not
   certification.
2. Freeze a legal/brand packet listing product naming, copied assets, external
   links, licenses, and excluded Notion trademarks; obtain the responsible
   review decision.
3. Bind a concrete deployment target, operator, backup destination, retention,
   restore-time objective, and rollback owner. Run the operational rehearsal
   only after explicit authorization and quiescence.
4. Run one allowlisted canary, monitor the adopted UI and Second Brain hard
   journeys, exercise the kill switch, and record rollback or promote. Broader
   rollout requires a separate decision.

## 5. Stop conditions

Stop before action if any step would access personal Notion content, live
`user_data`, `knowledge`, `.runtime`, or session data without explicit scope;
enable a flag; perform an in-place restore; start a migration; invite or message
another user; publish externally; or use an unredacted artifact. Preserve the
current default-off and non-release state on every refusal.

Business/Enterprise, Archive beta, native applications, billing, and private
Notion APIs remain conditional or excluded and do not block this Core profile.
