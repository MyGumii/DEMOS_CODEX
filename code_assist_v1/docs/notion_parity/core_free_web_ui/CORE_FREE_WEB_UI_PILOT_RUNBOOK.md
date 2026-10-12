# Core Free Web UI isolated pilot and recovery runbook

Status: offline synthetic rehearsal only  
Profile: `free-owner-chrome-desktop-ko-KR`  
Runtime registration: none  
Production feature flag: not created and not enabled

## 1. Purpose

This runbook governs the H9 rehearsal that proves the UI and Second Brain
journey can be piloted and recovered without touching personal or production
data. It is not authorization for a live canary, migration, resolver cutover,
public posting, invitation, payment, or Business/Enterprise trial.

## 2. Default-off authorization

The offline pilot library is not imported by `app_code.py`, routes, startup,
workers, or request paths. A new rehearsal must explicitly prove all of the
following before the first path lookup or filesystem mutation:

- opt-in is exactly `true`;
- kill switch is exactly `false`;
- synthetic-only confirmation is exactly `true`;
- scenario contract is valid;
- isolated browser suite is green;
- Second Brain hard gate is green;
- redaction is ready;
- backup is ready; and
- restore is ready.

Missing, false, extra, or malformed readiness fields deny the pilot. A
kill-switch value of `true` denies it before root inspection.

## 3. Data boundary

Only an absolute descendant of the active trusted OS test root is accepted.
The root must contain the exact frozen synthetic-pilot sentinel. Links,
junctions, unsupported filesystem entries, existing recovery destinations,
the repository, and all live `user_data`, `knowledge`, `.runtime`, session,
and workspace roots are refused.

Credentials, root paths, page/workspace/user/session IDs, titles, bodies, raw
DOM, cookies, tokens, and network logs never enter the repository report.

## 4. Pilot sequence

1. authorize one new isolated root;
2. create the frozen synthetic sentinel;
3. start the local fixture and register one synthetic user;
4. create and save one synthetic baseline page;
5. cancel a Second Brain preview and verify zero publication delta;
6. stop the server and confirm quiescence;
7. create a verified copy-on-write backup in a new root;
8. exercise the kill-switch denial while the server is stopped;
9. restart the active root as the canary;
10. edit the page and approve exactly one Second Brain publication;
11. stop the canary and retain that changed root for audit;
12. restore the backup into another new root without overwriting either prior
    root;
13. restart the restored root;
14. verify the baseline page body and zero publication counts;
15. cancel another preview and verify the Second Brain hard gate; and
16. emit only the identifier-free aggregate report.

## 5. Recovery and kill switch

Recovery never deletes or overwrites the changed canary root. Both snapshot
and restore require an explicit quiescence confirmation and compare the exact
tree fingerprint before accepting the new copy. If a copy, fingerprint,
sentinel, link, or path check fails, preserve every existing root and stop for
manual inspection.

The current kill switch is an offline harness authorization gate. There is no
production feature flag or runtime switch yet. Introducing one requires a
separate reviewed slice and explicit user authorization before it can be
enabled.

## 6. Allowed telemetry

Repository evidence may contain only:

- contract and synthetic alias;
- default-off/opt-in/kill-switch booleans;
- backup file count and verification booleans;
- baseline, canary, and restored item/revision counts;
- recovery state booleans;
- browser/test outcomes and SHA-256 of the identifier-free report; and
- source/checkpoint hashes.

Raw filesystem fingerprints bind the temporary copy internally but are not
persisted in the report because the copied tree contains synthetic credentials
and session material.

## 7. Support and incident handling

Stop the rehearsal immediately when any of these occurs:

- a path or sentinel check fails;
- personal or production content appears;
- an external request is observed;
- baseline preview changes Second Brain state;
- canary publication is not exactly one item and one revision;
- restored content differs from baseline;
- restored publication counts differ from baseline;
- auth, CSRF, origin, redaction, or accessible keyboard flow regresses;
- the host repeatedly terminates a validation process; or
- any live migration, flag, invite, payment, public posting, or resolver action
  becomes necessary.

Preserve the changed root, snapshot root, restored root, command result, and
identifier-free report only inside the trusted temporary environment for the
duration of diagnosis. Do not copy raw evidence into the repository.

## 8. Release and human-review boundary

The current rehearsal does not satisfy live canary readiness. Before any
broader rollout, all 25 adopted Core scenarios need reviewed reference
differentials, performance needs an approved budget, and a human reviewer must
complete legal, brand, accessibility, support, and rollback approval.

Current review state:

- automated local accessibility: green;
- formal accessibility audit: pending;
- proprietary Notion logo/trademark/illustration copying: excluded;
- legal/brand release approval: pending;
- synthetic offline recovery: green;
- operational backup/restore: not authorized;
- live pilot/canary: not authorized.
