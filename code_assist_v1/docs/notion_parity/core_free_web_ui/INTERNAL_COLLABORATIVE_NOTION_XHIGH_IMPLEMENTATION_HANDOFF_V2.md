# Internal collaborative Notion — xhigh implementation handoff v2

Status: authoritative implementation handoff for isolated, default-off work  
Recorded: 2026-08-23 Asia/Seoul  
Alignment revision: 2026-08-23 Asia/Seoul; predecessor SHA-256
`0a9f505a23d3d8da10f6ea2e1c69b2f16ac877c8dfd60d5b448bf1005dc51c31`  
Parent plan: `INTERNAL_COLLABORATIVE_NOTION_ULTRA_EXECUTION_PLAN_V3.md`  
Product completion matrix: `internal_collaborative_completion_matrix.v1.json`  
Execution packets: `internal_collaborative_packets.v2.json`  
Validation ladder: `internal_collaborative_validation_ladder.v1.json`

This document supersedes `INTERNAL_COLLABORATIVE_NOTION_XHIGH_IMPLEMENTATION_HANDOFF_V1.md`.
It converts the Ultra plan into an executable handoff; it is not implementation
evidence and authorizes no production or external-account mutation.

The authority order for execution is: the currently re-read
`code_assist_v1/AGENTS.md`, then the active Second Brain contracts and owning SBE
packet, then the Ultra parent plan, this handoff, and finally the machine-readable
catalogs. A lower authority never weakens a higher one. A conflict stops only the
conflicting packet and is recorded for the owning integrator; it is not silently
resolved by editing `AGENTS.md` or an SBE hot contract.

## 1. Product contract

Build an internal, desktop-web, Notion-like workspace/database UI connected to
Second Brain. This is not a request to reproduce Notion billing or its complete
backend business model. The required product behavior is:

- a user logged into the Agent host is the same subject on the paired Notion
  host; there is no second Notion account flow;
- internal users have one uniform adopted capability set, with no Free,
  Business, Enterprise, upgrade, or entitlement branch;
- uniform capability availability is not uniform data authorization: Notion
  workspace/page roles still determine Notion access, and SBE native-space roles
  independently determine Second Brain access;
- workspace membership, page sharing, inherited access, comments, mentions,
  inbox, presence, and convergent simultaneous editing are required;
- the adopted Notion UI states require interaction parity and exact deterministic
  pixel equality, not approximate visual similarity;
- Second Brain receives only an explicitly committed canonical page revision;
- coordinated backup, new-root restore, measured recovery, kill switch,
  rollback, and an internal multi-user canary are required;
- mobile, native apps, public web publishing, external email guests, legal
  review, and commercial release machinery are outside the current scope.

The completion matrix has 28 required rows. The original 23 rows are extended
with canonical permission-aware search, collaborative accessibility,
collaboration security, observability, and uniform internal capabilities.

## 2. Current facts that must not be rediscovered or reinterpreted

- Production pair: Agent `10011`, Notion `10012`.
- Development target pair: Agent `10111`, Notion `10112`; candidate dedicated
  collaboration gateway `10113` until the engineering freeze selects topology.
- The current 10111 preview launcher can copy `knowledge`, `user_data`, sessions,
  workspaces, and the auth registry. Therefore its root is not a safe synthetic
  collaboration fixture and must not be reused wholesale.
- Approved development identities may later be imported by allowlist, but no
  prior content, workspace, session, secret, token, audit log, blob, or runtime
  database is copied. The default allowlist is empty.
- Workspace schema `2026081317` is an IAM/locator/tree/lifecycle substrate. It is
  not the canonical collaborative page/block/comment/event authority and the
  runtime resolver has not completed cutover.
- Legacy page save is whole-page revision CAS. It does not prove simultaneous
  same-block or structural convergence.
- Current production hosting is Flask/Waitress and does not provide an approved
  WebSocket boundary or CRDT runtime.
- Existing browser journeys are primarily single-principal. Existing semantic
  differential evidence is not exact RGBA proof.
- Existing Core 25 work remains valuable behavioral foundation, but every
  required scenario must run again on the final collaborative candidate.
- Goal state is runtime state: query it at session start. Never replace or mark
  the goal complete merely because a packet or this handoff is complete.
- At this alignment checkpoint the runtime goal is paused on thread
  `019ff607-9116-78a1-9f40-c580c87c0038`, while the concurrently owned SBE
  `STATUS.md` still records that its planning handoff observed no active goal.
  Runtime state governs execution; this stream does not edit the status file.
  The assigned status integrator must reconcile the observation before any
  shared-goal completion claim.
- The current SBE contract baseline remains 14 goals and 124 mandatory scenarios;
  only `SBE-0.1` may perform its coordinated successor-count transition.

## 3. Correct executable dependency graph

```text
U0a inventory + draft denominator + validation/completion harness
  |-- D0a synthetic pair on OS-assigned ports
  |-- H0a four-principal fixtures, then independent browser leak oracles
  |-- A0a authority ADR and disposable atomic prototype
  |-- E0 candidate corpus -> benchmark/fault report -> pinned winner ADR
  |-- T0a ticket/envelope/epoch/sequence gateway prototype
  |-- O0a generic backup prototype -> selected-format restore benchmark
  `-- UI0 behavior-preserving editor host adapter

G0-U product-owner decisions + G0-E engineering decisions -> G0 aggregate freeze

G0 + UI0
  `-- K0 aggregate/capability default-off control plane

G0 + synthetic gates + K0
  |-- D0b approved identity-only dev import and 10111/10112/10113 switch
  |-- A0b next forward schema, import, default-off canonical cutover
  `-- T0b durable store -> gateway -> cross-process/browser closure

D0b + A0b + T0b + K0
  `-- I0 workspace/page IAM and share/revoke
       `-- C0 presence/comments/reactions/mentions/inbox

I0 + T0b + E0 + UI0 + K0
  `-- E1 canonical commands -> editor UI -> convergence/fault closure

E1 + I0 + K0
  |-- S0 Notion-side exact-revision adapter and Second Brain fence
  `-- F0 database -> file -> history -> search/adjacent -> adopted UI surface

V0A exact comparator + V0R approved reference capture + all visible slices
  `-- V0B final Core 25, multi-user, accessibility, and pixel closure

O0a + A0b + T0b + I0 + C0 + E1 + S0 + F0
  `-- O1A local coordinated restore -> O1B approved external-target drill

V0B + O1B + K0
  `-- Q0 target load/security/fault/observability gate
       `-- R0A approved production-copy rehearsal
            `-- R0B approved default-off production install
                 `-- R1 approved canary and prebuilt kill-switch exercise
                      `-- Z0 strict 28-row completion gate
```

The packet catalog separates `matrix_contributes_to` from `matrix_closes`.
Fixtures, spikes, implementations, comparators, historical tests, and approvals
may contribute evidence but never close a product row by themselves. Each of
the 28 rows has one planned closure owner. Only `Z0-P1` may claim product
completion after strict receipt verification.

The graph also has cross-stream gates that are not represented by an arrow above:
`D0B-P1` and SBE `SBE-7.2` require an exclusive 10111 activation handoff;
`S0B-P1` requires an accepted owning SBE packet and rollout gate; and
`O1B-P1`, `R0A-P1`, `R0B-P1`, and `R1-P1` require coordinated ownership with
`SBE-7.2` plus the rebaselined target-authority work corresponding to P7.8,
P7.9, and P8. Approval alone never satisfies these ownership gates.

## 4. Work that can proceed before the interview

The following work is synthetic, local, default-off, and does not need access
to current development identities or production data:

- `U0A-P1..P2`: finish the live authority/capability inventory and implement the
  packet, ladder, completion, hash-drift, duplicate-key, and false-green guards;
- `D0A-P1..P3`: pure pair config, generated accounts/new roots, and launch tests
  on OS-assigned fixture ports—not literal `10111/10112`;
- `H0A-P1..P2`: four principals, independent browser contexts, outsider control,
  leak oracles, deterministic reset, and owned-process cleanup;
- `A0A-P1..P2`: one-authority/revocation ADR and unversioned disposable SQLite
  atomicity/restart prototype;
- `E0-P1..P4`: candidate matrix, shared convergence/IME corpus, isolated runtime
  adapters, fault/cost benchmark, pinned winner ADR;
- `T0A-P1..P3`: ticket/envelope/epoch/sequence/ACK/replay/backpressure contract,
  transport-only prototype, and selected-runtime binding plan;
- `O0A-P1..P3`: coordinated component inventory, concurrent-writer/failpoint
  backup prototype, and selected CRDT-format restore benchmark;
- `UI0-P1`: behavior-preserving editor host adapter with six Node regressions and
  the historical browser journey;
- `V0A-P1`: strict deterministic RGBA comparator and reviewed-mask verifier.

The actual current dev registry, literal dev ports, existing 10111 process, and
real 10112/10113 start are reserved for `D0B-P1` plus `AP-DEV-PAIR`.

The immediate executable packet is `U0A-P1`. The earlier draft incorrectly
pointed at `U0A-P2` even though its prerequisite had not been completed.

## 5. G0 decision freeze

G0 has two independently recorded receipts.

### G0-U — product-owner decisions

1. Development identity import allowlist, or confirmation that all test
   identities are disposable.
2. Target topology and scale: registered users, simultaneous connections,
   simultaneous editors per page, pages/workspace, blocks/page, rows/database,
   sustained and burst operations/second, attachment concurrency, and disk
   reserve.
3. Adopted, deferred, and hidden feature denominator.
4. RPO, RTO, write-drain limit, backup destination/key owner/retention, page
   history retention, and restore secret policy.
5. In-tab transient disconnect buffering or durable browser-close offline edits.

Provisional spike values are one Windows host, 50 connections, 10 simultaneous
editors on one page, in-tab buffering only, RPO 15 minutes, RTO 60 minutes,
write drain at most 60 seconds, and no automatic deletion of user-visible page
revisions. These are benchmark inputs, not release promises.

Provisional adopted features are Core 25, workspace/page sharing, live comments,
mentions and inbox, history/restore, files, table/board/list/gallery/calendar/
timeline, filter/sort/group, relation/rollup/formula, templates, Markdown/CSV
import/export, reminders, and permission-aware search/backlinks. Provisional
hidden features are charts, dashboards, forms, automation, feed, map, public
links, external email guests, and AI.

### G0-E — engineering decisions

Engineering freezes the canonical authority design, gateway/CRDT winner,
protocol/update/snapshot/compaction format, and next forward schema version.
The schema number is derived by re-reading the current migration lineage under
an exclusive canonical-store ownership lease; it is not a user interview item.

`A0A-P2`, `E0-P4`, `T0A-P3`, and `O0A-P3` are required evidence for G0-E. Product
implementation packets require both G0-U and G0-E.

Recommended restore-secret policy: rotate the session-signing secret and force
reauthentication after restore. A copied session registry must not silently
revive pre-restore sessions.

## 6. Architecture contract

### 6.1 Paired identity and environment

Use a new, empty, non-reparse, marker-bound development root. After approval,
copy only the minimum registry fields and password hashes for named disposable
development accounts. Generate a new development signing secret and a cookie
name distinct from production. The Agent host owns account mutation; the Notion
host denies signup and account mutation.

Peer navigation and mutation authorization are separate. Exact cross-port links
may be configured, but a `10111` Origin does not authorize a `10112` unsafe
request. Missing or invalid configuration disables links and ticket issue; it
never falls back to `10011/10012`.

### 6.2 One canonical content/IAM/event authority

The preferred design extends the physical **Notion workspace** SQLite authority
that owns Notion IAM. It does not replace, replicate, or become a shard of the
central SBE SQLite database. One Notion transaction commits:

- current membership/page-role evaluation and workspace/page access epochs;
- operation ID, canonical request hash, replay result, and document sequence;
- canonical page/block/database/comment mutation and relevant revision identity;
- audit, outbox, and durable collaboration event;
- content-addressed snapshot/blob metadata and publication eligibility.

Legacy owner shards become immutable import sources or generated compatibility
projections. They are never a second write authority. Request-time directory
scans and dual writes are forbidden.

A fallback owner-shard/single-coordinator design is admissible only if the E0/A0
evidence proves cross-database permission fencing, immediate revoke, restart
recovery, no directory scans, and single-writer deployment.

### 6.3 Gateway, CRDT, and protocol

Freeze a dedicated WebSocket boundary, not its language. E0 compares at least:

- Python ASGI with a Windows/Python 3.14-compatible CRDT binding;
- Python ASGI opaque relay with a separately verified compactor;
- Node/Yjs-native gateway with the canonical Python command boundary.

The gateway never treats a Flask cookie as direct authorization. The Notion
host issues a short-lived, one-time, CSRF-protected ticket bound to subject,
workspace, page, client/session, capabilities, workspace/page epochs, JTI,
issued time, and expiry. Store only the digest needed for one-time consumption.

Use a mature CRDT for rich-text leaves and server-sequenced typed commands for
block-tree and database-schema changes. Do not invent route-level OT/merge.

Keep these identities distinct:

- client operation ID for retry and deduplication;
- per-document event sequence for durable order;
- CRDT/update generation for wire-format identity;
- checkpoint/snapshot ID for replay and compaction;
- document revision for a user-visible content milestone;
- Second Brain publication revision for the immutable published artifact.

Compaction may discard operations only after a verified snapshot, event
high-water mark, coordinated backup generation, replay proof, and retention
rule. It never invalidates an immutable publication revision.

### 6.4 Immediate revocation

A revoke transaction durably increments the relevant epoch. Completion then
requires gateway fence propagation plus either active-connection acknowledgement
or an authoritative epoch check immediately before every send. Define the
response when the gateway is unavailable. After the revoke response, old-epoch
writes, replay, queued frames, subscriptions, notifications, inbox rows, search
snippets, cache reads, unpublished previews, new publication attempts, and live
Notion attachment reads must deliver zero protected data.

A successful earlier publication is different: it created an independent SBE
item and revision history. A later Notion revoke must not hide, deactivate, or
delete that already-published SBE item. Its subsequent visibility and lifecycle
are governed only by current SBE space authorization and SBE lifecycle rules.

### 6.5 Second Brain boundary

Prefer a Notion-side adapter that captures an exact canonical document revision
and access epoch. `knowledge_core/**`, existing Second Brain evolution contracts,
and `app_code.py` remain read-only dependencies. Only if `S0A-P1` proves a
protected change unavoidable may conditional `S0B-P1` acquire an explicit file
ownership transfer and run the full Second Brain evolution regression.

Notion workspace roles and SBE personal/Part/Team/company roles are separate
authorization domains that share only the authenticated subject identity.
Notion revision IDs, event sequences, history, search, and schema lineage are
separate from SBE item IDs, revision histories, lifecycle, retrieval, and v8-v11
schema lineage.

Publication follows this ordered contract:

1. Resolve one exact committed Notion revision, canonical hash, and access epoch.
2. Revalidate current Notion source authorization and publication eligibility.
3. Call the SBE service-layer publication API with a caller-supplied
   `operation_id` and canonical request hash; routes and jobs never write SBE
   canonical tables or projection files directly.
4. Revalidate the target SBE native-space role at commit time. Notion eligibility
   never grants target-space authority.
5. For personal+Part, create two independent SBE items and revision histories in
   one SBE transaction, with audit and durable outbox committed atomically.
6. Retry after an ambiguous response returns the original result for the same
   operation ID/hash and rejects reuse with a different hash. Later Notion edits
   never auto-synchronize the published items.

An ADR owned before `S0A-P2` must freeze cross-database publication linearization,
revocation races, and the crash-after-SBE-commit response-loss case. Expensive
rendering, file copy, OCR, embedding, LLM, and network work stays outside both DB
transactions and its inputs are revalidated immediately before commit.

A Notion page may affect an Agent conversation only through an already-published,
currently authorized SBE item. Attaching a Notion page pins that immutable SBE
item revision; it neither grants access nor inserts the live Notion source into
the corpus. One conversation remains bound to one selected Wiki/view, and a
selection change creates a new conversation. Server-owned `CorpusClosure`, at
most ten reranked results, at most four separately labeled pins/stable references,
and the 32 KiB combined excerpt budget remain unchanged.

Notion page timestamps are UI/projection metadata. They do not enter an SBE
canonical body, content hash, FTS, deduplication, semantic analysis, reranker text,
or Agent excerpt. Any central SBE schema change remains forward-only,
checksummed, idempotent where applicable, single-migrator, and owned by the
corresponding active SBE packet; this plan never edits historical v1-v7 contracts.

## 7. Default-off, recovery, and release ordering

The Notion aggregate/capability kill-switch control plane is implemented in
`K0-P1`, before canonical and user-facing product slices. It controls only new
Notion capabilities and never sets, aliases, or bypasses an SBE successor flag.
Every later Notion packet registers a default-off capability. `Q0` tests
precedence and fault behavior. `R1` exercises the already-built switch; it does
not introduce the switch during canary.

Backup development is split:

- `O0`: design and synthetic prototype across canonical Notion DB, Second Brain
  DB, identity registry, event/snapshot stream, blobs, concurrent writers, and
  failpoints;
- `O1A`: local coordinated generation and new-root application-level restore;
- `O1B`: approved encrypted external destination and target-host drill;
- `R0A`: approved offline production-copy rehearsal without production writes;
- `R0B`: separately approved default-off production install and service health;
- `AP-PROD-RESTORE`: incident-only approval for an actual production restore,
  never implied by rehearsal, deployment, or canary approval.

The Notion and SBE databases remain separate authorities during coordinated
backup. A generation manifest records each independently verified database and
its agreed publication/high-water boundary; it does not claim a cross-database
SQLite transaction. The active SBE backup/restore packet owns SBE backup format,
retention, and restore semantics. All pytest suites use synthetic, sanitized,
runner-owned roots. An approved production copy is used only by offline
migration/restore and redacted operator smoke actions, never as a pytest fixture.

## 8. Validation and evidence identity

The validation runner has two modes:

- `audit` checks schemas, references, hashes, receipts, and honest incompleteness;
- `gate` fails unless all required evidence and decisions are complete.

An audit success is never completion evidence. Existing baseline commands are
supporting evidence only until the new real cross-process, multi-principal,
exact-pixel, coordinated-restore, target-load, and canary lanes close their rows.

All realms share one `release_candidate_id` derived from product source,
dependency locks, schema/migrations, collaboration protocol, and frontend build.
They do not share a literal configuration. Each realm has an approved redacted
configuration fingerprint and proof that it derives from the same configuration
contract. Canary and full rollout share the promoted
`deployment_generation_id`; rebuilding after canary invalidates canary evidence.

Every receipt includes exact argv, realm, candidate/config/source hashes,
toolchain, start/end, signed and hexadecimal exit status, test counts, artifact
hashes, source pre/post hashes, retry classification, isolation, and owned-PID/
port cleanup. It never contains raw cookies, tokens, passwords, private titles,
personal paths, or unredacted HAR/trace data.

Receipts that touch an SBE seam also bind the observed `AGENTS.md` hash, current
goal observation, the ordered 18-document SBE preflight manifest, active SBE
packet/status snapshot, SBE contract bundle hash, and current schema lineage.
Tests carry separate `matrix_row_ids` and exact `TEST_OPERATIONS.md` scenario IDs
in names or markers. Publication, locking, job claiming, backup, and recovery
claims require real distinct processes and receipt fields for PID, role, process
count, shared DB identity, and exercised fault point. Target Windows receipts
record p50/p95/p99, SQLite busy counts, queue/projection lag, and restore timing.

### Exact pixel contract

The existing semantic differential cannot close `PIX-001`. The new verifier
decodes sRGB RGBA8 and requires equal viewport, device scale, dimensions, fonts,
locale, zoom, time, animation state, and build. It permits only named, reviewed,
scenario-bound, content-independent masks. Any unmasked differing pixel fails.
Receipts include image/mask/diff hashes, differing-pixel count, bounding boxes,
geometry, focus, and accessibility evidence at `1440x900` and `1280x800`.

### Windows crash policy

For `-1073740791` / `0xC0000409`, preserve the original receipt, clean only
runner-owned PIDs/ports/handles, and retry once in a fresh process, temp root,
port set, and browser context. A retry pass is `host_flaky`, not a pass. It needs
one additional clean run on the same candidate (two clean runs total after the
crash) before closure. A second crash is infrastructure-blocked. Assertions are
normal code failures and are not retried.

## 9. Ownership discipline

Every path has one active owner. Before `in_progress`, a packet records owned
existing files, new product/test files, read-only and protected dependencies,
pre-edit SHA-256 values, owner, lease, and rollback/cleanup. Re-read
`AGENTS.md`, goal state, current schema lineage, packet catalog, and authority
hash checkpoint at packet start.

For SBE-adjacent work, ownership is checked across both the Notion packet catalog
and the active SBE ownership/status snapshot. The lease records exact paths,
pre-edit hashes, owner, expiry, and external prerequisites. Drift, a second owner,
or an unassigned SBE owner fails closed. `U0A-P2` must implement this verifier
before any product implementation packet begins.

This stream does not edit `AGENTS.md`, shared `STATUS.md`, SBE traceability, or
other hot shared contracts. Each execution packet writes only its predeclared
evidence/checkpoint file; the separately declared status/contract integrator is
the sole writer that later folds accepted evidence into shared status. A dynamic
`AGENTS.md` hash change is recorded and re-read, not treated as permission to
overwrite the other stream's file.

- RUNTIME: pair config, auth/session composition, origins/CSRF, launchers,
  gateway lifecycle, and health. `app_code.py` requires explicit transfer.
- CANONICAL: workspace store, migration lineage, commands, events, epochs,
  audit, outbox, resolver, import, and compatibility projection. Only one writer
  may own the store or next migration.
- UI: `notion.js`, `notion.css`, `index.html`, editor core, host adapter, and
  collaboration UI. Run six Node contracts and the browser journey after shared
  UI changes.
- SECOND_BRAIN_PRODUCTION: protected Second Brain, production configuration,
  backup destination, service packaging, and rollout. Explicit ownership and the
  packet-specific approval are mandatory.
- DOC_HARNESS/SPIKE: different new files may be parallel, but a shared file still
  has one owner; spike dependencies never become production choices before G0-E.

Do not modify `knowledge/`, `user_data/`, `.runtime/`, existing user roots, the
current 10111 preview root, or production during interview-free packets.

## 10. Approval ledger

- `AP-DEV-PAIR`: approved identity allowlist, new dev roots and namespace,
  replacement of dev 10111, and start of 10112/10113.
- `AP-RUNTIME-SPIKE`: only when an exact candidate dependency is unavailable and
  must be downloaded/installed in the isolated spike.
- `AP-REFERENCE-MULTIUSER`: mutate and clean synthetic states only in the
  dedicated external Notion reference workspaces.
- `AP-OPS-TARGET`: external encrypted backup destination, key, retention, and
  target-host restore drill.
- `AP-PROD-REHEARSAL`: approved offline production-data copy; no production
  mutation.
- `AP-PROD-DEPLOY`: frozen artifact default-off install, gateway service install
  or restart, and health check; no canary enablement.
- `AP-PROD-CANARY`: named collaborators, outsider, workspace, duration, abort
  thresholds, kill switch, and rollback.
- `AP-PROD-RESTORE`: incident-specific actual production restore only.

Approval authorizes the exact external mutation or protected boundary. It is
never test evidence. Every approval receipt binds scope, roots, ports, subjects,
workspace, commands, forbidden scope, precondition hashes, expiry, rollback,
and hash-drift invalidation.

Approval also does not confer file ownership. A packet may proceed only when both
its approval (if any) and every local/cross-stream ownership lease and external
gate are valid.

## 11. Exact resume instruction

1. Re-read `code_assist_v1/AGENTS.md` and hash it.
2. Query current goal state without replacing it.
3. Verify the newest superseding checkpoint hash bundle and strict-parse the
   matrix, packet catalog, and validation ladder.
4. Read the current SBE `STATUS.md`, active execution catalog, and ownership
   snapshot. For any SBE/central-schema seam, read all 18 documents listed by
   `AGENTS.md` in order and bind their manifest to the packet receipt.
5. Acquire only the DOC_HARNESS files needed by `U0A-P1`; do not acquire
   `AGENTS.md` or an SBE hot contract.
6. Finish the current route/storage/authority inventory, capability/state draft,
   and open-decision register.
7. Run the declared pre-edit baseline and audit mode.
8. Record a hash-bound packet checkpoint; only then advance to `U0A-P2`.

Only `U0A-P1` and then `U0A-P2` are safe before the cross-catalog verifier exists.
After `U0A-P2`, parallel work may begin on D0a/H0a, A0a/E0/T0a/O0a, UI0, and
V0A when their explicit local and external prerequisites are satisfied. Stop at
the first
dependency download, external reference mutation, real identity/root/service
mutation, backup-target operation, or production action and request the named
approval packet.

## 12. Honest completion claim

This handoff and its catalogs make implementation resumable; they do not make
the product complete. At handoff time no new collaboration product packet or
phase gate is claimed passed. Product completion exists only when `Z0-P1` runs
strict gate mode on the frozen release and verifies all 28 rows, all phase gates,
all required approvals, realm derivations, exact pixels, recovery, load/security,
production-copy rehearsal, canary, rollback, and deployment-generation binding.

`Z0-P1` closes only the internal collaborative Notion product contract. The
currently active shared goal may be marked complete only when Z0 is closed **and**
the then-current `TEST_OPERATIONS.md`, `TRACEABILITY.md`, and
`GOAL_CONTRACT.json` SBE gates are satisfied. A paused goal remains paused; this
handoff and its checkpoint do not resume, replace, block, or complete it.
