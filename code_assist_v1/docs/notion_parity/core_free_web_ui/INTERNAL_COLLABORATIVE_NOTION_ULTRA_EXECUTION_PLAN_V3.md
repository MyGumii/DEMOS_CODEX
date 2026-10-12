# Internal collaborative Notion + Second Brain — Ultra execution plan v3

Status: implementation-planning authority; no live rollout authority  
Recorded: 2026-08-23 Asia/Seoul  
Alignment revision: 2026-08-23 Asia/Seoul; predecessor SHA-256
`16d15a2294b72ff3cda9a95006baaefca689059ea8ca20b5e399f6337036446c`  
Primary profile: internal Windows desktop, Chrome, `ko-KR`  
Deployment pairs: development `10111/10112`, production `10011/10012`

This document supersedes only the implementation order and completion claims in
`INTERNAL_COLLABORATIVE_NOTION_SCOPE_V2.md`. It preserves the accepted product
intent and the historical Core UI evidence. It does not edit or supersede
`AGENTS.md`, the Second Brain evolution contracts, frozen schema migrations, or
any production configuration.

No step in this plan authorizes a live migration, service restart, feature-flag
transition, production backup, production restore, or modification of personal
content. Those actions remain separate approval packets.

## 1. Executive verdict

The latest product direction is sound:

- build an internal Notion-like collaborative database, not Notion's billing or
  plan-entitlement system;
- use the same Second Brain account identity on each paired host;
- give every active internal user the same adopted capabilities;
- develop and verify on `10111/10112`, then promote reviewed code and migrations
  to `10011/10012` without promoting development users or content;
- require real multi-user sharing, simultaneous editing, controlled pixel
  equality, Second Brain integration, and operational recovery;
- defer mobile and native applications.

The prior implementation order is not safe enough. The current page content and
page-share authority still live in legacy per-owner Notion shards, while the
workspace schema is an IAM, locator, navigation, lifecycle, audit, and outbox
substrate. The current editor saves a whole page with revision CAS. Adding share
UI and live events directly on top of those split authorities would make
permission revocation, event ordering, and content convergence impossible to
prove atomically.

The corrected order is therefore:

1. isolate the development pair;
2. choose and prove one canonical page/content/IAM/event authority;
3. complete sharing and revoke semantics against that authority;
4. add a durable real-time transport;
5. add presence and asynchronous collaboration;
6. add a proven convergent editor;
7. finish adopted database, file, history, and Second Brain behavior;
8. pass recovery, load, pixel, security, and internal-canary gates.

## 2. Product boundary

### 2.1 Required product

- Desktop workspace shell, sidebar, page tree, Trash, editor, database views,
  search, comments, sharing, permissions, page history, attachments, and
  permission-aware loading/empty/error states.
- Existing internal accounts only. Workspace members and page-only internal
  guests are Second Brain subjects; there is no separate Notion account.
- The authenticated subject identity is shared, but Notion workspace/page roles
  and SBE personal/Part/Team/company authorization are independent. Uniform
  feature availability never bypasses either data-authorization domain.
- Workspace and page role lifecycle, direct and inherited access, group and
  teamspace access, immediate revoke, and non-disclosure to outsiders.
- Live presence, comments, reactions, mentions, inbox delivery, reconnect, and
  simultaneous text/block/database editing.
- Notion page revisions and Second Brain publication revisions remain separate,
  immutable identities.
- Controlled-environment pixel equality for every adopted visible state.
- Coordinated backup, isolated restore, integrity checks, and rollback drills.

### 2.2 Uniform capabilities, not plan emulation

There are no Free, Plus, Business, or Enterprise product tiers; no seat or
workspace quotas; and no plan-specific upload or history limits. Reference
Notion accounts are used only to observe UI and behavior.

"No product quota" is not "unbounded resource consumption." Uploads, event
payloads, operation rates, retained history, open connections, and disk reserve
must have operator safety limits. A safety refusal is a capacity or security
error, never an upgrade prompt.

### 2.3 Deferred or conditional

- Deferred: mobile web parity, native applications, full browser-close offline
  authoring unless explicitly selected later.
- Conditional: public/anonymous publishing, external email guests, SAML/SCIM,
  DLP/SIEM, Enterprise administration, Notion AI, automations, and paid-only
  reference surfaces.
- Internal accessibility, security, dependency-license, and recovery gates are
  retained even though a formal external legal/brand launch gate is excluded.

## 3. Authoritative current-state findings

### 3.1 Development identity and environment

- Both Flask hosts currently default to host-only cookie name `session` and use
  the secret and users registry below `CODE_ASSIST_DATA_ROOT/.runtime`.
- Browser cookies are scoped by host, not port. Different secrets do not prevent
  `session` cookie-slot replacement between development and production.
- Several Agent/Notion links still embed ports `10011` or `10012`.
- CSRF is same-origin and session-bound. A shared login does not authorize
  cross-origin unsafe requests; peer navigation origins and mutation-origin
  allowlists must remain separate concepts.
- The existing full Second Brain preview copies package `knowledge`,
  `user_data`, `sessions`, `workspace`, and the auth registry. It is not a
  synthetic-only collaboration root.

Therefore the approved idea is **development-account reuse**, not reuse of the
current preview data root. A new paired root must contain only approved
development identities plus synthetic Second Brain and Notion data.

### 3.2 Storage and authorization

- Workspace schema `2026081317` provides substantial IAM, binding, navigation,
  tree/lifecycle, operation, audit, outbox, and offline-rehearsal foundations.
- Runtime resolver cutover, continuous source consumer, destructive watermark
  acknowledgement, and live migration are still disabled/unregistered.
- Canonical page blocks, comments, database rows, and collaboration operations
  are not owned by the workspace catalog today.
- Legacy page reads and saves can discover/open per-owner shards, and page saves
  replace the whole block set under a page revision CAS.
- Workspace IAM and legacy page shares are therefore related foundations, not
  yet one page-access authority.

### 3.3 UI evidence and rollout controls

- The existing 25 scenarios remain useful, reviewed single-user contracts.
- Their historical result cannot certify the final collaboration build. Every
  scenario must be rerun against the final source and configuration hashes.
- Existing pilot control primarily gates workspace shell/navigation/tree. It is
  not yet an aggregate kill switch for editor, sharing, comments, files, and
  collaboration transport.

### 3.4 Runtime transport

- Production hosting is Flask/Waitress WSGI; the pinned runtime has no WebSocket
  or CRDT dependency.
- Holding one Waitress worker thread per long-lived SSE connection is not the
  default architecture for the required collaboration scale.
- The current inbox timer is polling, not multi-user live-delivery evidence.

## 4. Architecture decisions to make before feature implementation

### 4.1 One canonical authority

The final collaboration path must evaluate membership, page access, canonical
content, operation deduplication, event sequence, and audit/outbox from one
transactional authority. Request-time directory scans and dual writes are
forbidden.

The architecture ADR must compare at least:

1. **Preferred candidate — workspace-canonical collaborative documents.**
   Import active legacy page snapshots into forward-versioned canonical
   workspace document/block/database/comment tables. Store accepted operations
   and their event sequence in the same database as access epochs. Keep legacy
   storage read-only during cutover and generate compatibility projections; do
   not dual-write.
2. **Fallback candidate — owner-shard canonical operations with a single
   coordinator.** This is acceptable only if a cross-database permission fence,
   immediate revoke ordering, crash recovery, no-directory-scan resolution, and
   one-writer deployment are proven. A best-effort membership check before a
   shard write is insufficient.

The selected design must prove migration, restart, replay, revocation, backup,
and Second Brain snapshot behavior before a schema version is allocated. The
historical candidate version `2026081318` must not be reused silently.

### 4.2 Real-time gateway

The preferred transport is a dedicated ASGI/WebSocket collaboration gateway,
run as a separate explicit service during development and placed behind a
same-origin reverse proxy or equally strict deployment boundary in production.
The existing Flask applications remain the REST and static hosts until a
separate migration approves otherwise.

Connection authentication uses a short-lived, one-time collaboration ticket
issued by the authenticated Notion host after CSRF validation. The ticket is
bound to subject, workspace, page, access epoch, client/session ID, expiry, and
allowed capabilities. The gateway must not treat possession of the Flask cookie
alone as sufficient authorization.

Every durable message has a client operation ID and per-document monotonic
sequence. Clients acknowledge sequence numbers, deduplicate retries, replay
from the last acknowledgement, or receive a bounded canonical snapshot after
compaction. Payload size, rate, queue depth, and reconnect storms are bounded.

SSE plus HTTP is a fallback only if an ADR proves ordering, backpressure,
connection capacity, restart recovery, and the target concurrency on Windows.
Adding long-lived SSE directly to the current 16-thread Waitress host is not an
accepted default.

### 4.3 Convergent editor

Whole-page last-writer-wins or reload-on-conflict is not the real-time editing
protocol. Before schema work, a technology spike must run the exact convergence
corpus against candidate implementations.

- Prefer a mature CRDT family such as Yjs or an equivalent reviewed library.
- Verify the chosen client/server binding on the pinned Windows, Chrome, and
  runtime versions; do not assume Python 3.14 or Node deployment compatibility.
- Server-authoritative block structure and database schema operations may wrap
  text CRDTs, but ordering and move/delete-vs-edit outcomes must be deterministic.
- Do not create a new ad-hoc OT or merge algorithm in route code.
- Separate high-frequency event sequence from user-visible document revision.
  A compaction/checkpoint can advance a document revision; every keystroke must
  not become a full history snapshot or Second Brain publication revision.

### 4.4 Revocation and permission fencing

- Permission changes advance an exact workspace/page access epoch in the same
  canonical transaction as audit and outbox.
- An edit, replay, snapshot, search result, comment delivery, and subscription
  is accepted only against the current epoch.
- Queued events are checked again immediately before delivery.
- A revoke response is complete only after the new deny state is durable and
  all old-epoch reads, writes, replay, cached notifications, and subscriptions
  are fenced.
- The fence also blocks unpublished preview, live Notion attachment reads, and
  new publication. It does not hide, deactivate, or delete an SBE item that a
  prior successful publication created independently; that item is governed by
  current SBE authorization and lifecycle.
- Outsiders receive the same non-oracle response for absent and unauthorized
  workspace/page/member identities.

### 4.5 Files and history

- Replace buffered host request ceilings with bounded-memory multipart or
  resumable chunk ingestion before claiming unrestricted internal files.
- Reserve capacity before accepting bytes; checksum, content validation, final
  atomic publication, cancellation, and orphan cleanup are required.
- Retention is an operator policy, not a plan tier. Page history must define
  snapshot cadence, compaction, restore, immutable publication references, and
  whether automatic deletion is allowed.

### 4.6 Second Brain boundary

- A preview binds one committed Notion document revision and exact permission
  epoch. Approval publishes that revision or fails stale.
- Cancel produces no item or revision.
- Attach/detach, session reopen, search, backlinks, citations, and publication
  targets are reauthorized server-side.
- The concurrent Second Brain evolution package is a protected dependency. Each
  Notion slice records its input hashes and reruns its publication/session
  regression; it does not edit Second Brain contracts without ownership transfer.
- Publication revalidates Notion source authority, calls the SBE service layer
  with caller `operation_id` and request hash, then revalidates target SBE
  native-space authority. Personal+Part creates two independent SBE items and
  histories in one SBE transaction; later Notion edits never auto-sync them.
- The Notion workspace database and central SBE SQLite remain separate canonical
  authorities and schema lineages. Any SBE v8-v11 change belongs to the active
  owning SBE packet and rollout gate; historical v1-v7 contracts stay unchanged.

## 5. Ultra phase DAG

```text
U0 product denominator and decision register
  └─ D0 sanitized 10111/10112 paired environment
       ├─ A0 canonical authority + runtime resolver ADR/spike
       ├─ H0 multi-principal/browser harness
       ├─ E0 CRDT/editor technology spike
       └─ O0 backup-consistency and compaction design

A0 + H0
  ├─ I0 workspace/page IAM and share/revoke vertical slice
  └─ T0 durable collaboration event kernel + authenticated gateway

I0 + T0
  └─ C0 live presence/comments/reactions/mentions/inbox

I0 + T0 + E0
  └─ E1 convergent text/block/database editing

E1 + I0
  ├─ S0 exact-revision Second Brain integration
  └─ F0 streaming files, page history, and adopted database surface

Every visible slice ──> V0 reference/local interaction and pixel loop

A0 + E1 + S0 + F0
  └─ O1 coordinated backup and isolated restore

I0 + C0 + E1 + S0 + F0 + V0 + O1
  └─ Q0 load/security/fault/accessibility gate
       └─ R0 production-data-copy rehearsal
            └─ R1 multi-user internal canary and rollback exercise
```

## 6. Phase contracts

### U0 — Product denominator and decisions

Deliverables:

- machine-readable capability/state catalog;
- exact role/permission matrix;
- required/deferred/conditional/excluded database-feature list;
- target scale and deployment topology;
- RPO/RTO, retention, and offline-edit policy;
- expanded completion matrix and strict verifier.

Gate: every completion claim has a finite denominator and named evidence. The
old 25 scenarios are linked as `historical_single_user_foundation`, never as the
new completion denominator.

### D0 — Sanitized paired development hosts

1. Inventory metadata only; never reuse copied personal content as a fixture.
2. Create a new, external, non-reparse, marker-bound paired root.
3. Import only user-approved disposable development identities. Do not copy
   sessions, audit, knowledge, user data, workspaces, blobs, projections, or
   the old secret.
4. Generate one new dev secret and one dev-only cookie name shared by 10111 and
   10112. Keep production default behavior unchanged.
5. Use one canonical hostname and strict `CODE_ASSIST_CODE_ORIGIN` and
   `CODE_ASSIST_NOTION_ORIGIN` values. Invalid/missing config disables peer
   navigation rather than falling back to production ports.
6. Make 10111 the only account-mutation authority for the pair, or keep the
   registry immutable during the harness. 10112 never creates a separate user.
7. Start both services only after isolated tests pass; all new product flags
   remain off.

Gate: user A logs in at 10111 and is the same subject at 10112; user B in a
separate browser context remains B; dev/prod cookies, roots, secrets, CSRF,
logout, links, and network requests cannot cross; production before/after hashes
are unchanged.

### A0 — Canonical authority and dev-only cutover

- Freeze an ADR and exact schema/migration plan.
- Allocate the next available version only after re-reading current frozen
  schema lineage and other developers' ownership.
- Import a deterministic synthetic corpus into the candidate authority.
- Cut reads and writes over in the dev pair only; no compatibility dual-write.
- Prove no request-time owner-directory scan and no second permission authority.
- Prove response-loss replay, restart recovery, source/canonical hash equality,
  and fail-closed rollback.

Gate: one dev-only resolver is authoritative for content and access, and all old
mutation paths are disabled or compatibility-read-only under the aggregate kill
switch.

### H0 — Multi-principal harness

Use at least owner, member, internal page-only guest, and outsider negative
control identities. Run at least two independent real browser contexts
simultaneously; use additional contexts when a scenario needs all roles.

Gate: no cookie, cache, service worker, local storage, page title, member name,
or existence leak across principals or environments.

### E0 — CRDT/editor spike

- Compare deployable candidate libraries and gateway bindings.
- Execute same-block insert/delete/format, different-block edit, structural
  move/delete-vs-edit, database cell/schema, Korean IME, undo/redo, reconnect,
  duplicate/out-of-order, crash/restart, and compaction cases.
- Measure state bytes, operation rate, snapshot time, replay time, and memory.

Gate: deterministic canonical hash convergence and zero character loss. A
failed candidate leaves no production dependency or frozen schema behind.

### O0 — Backup and compaction design

Define the consistency barrier while A0/E0 schemas are still changeable. The
mandatory set is canonical databases, non-rebuildable blobs, collaboration
events/snapshots, legacy source retained for cutover, identity registry, exact
schema/config markers, and encrypted key metadata. Rebuildable projections are
verified by regeneration rather than treated as the sole backed-up truth.
The coordinated manifest binds independently verified Notion and SBE databases
at an agreed publication/high-water boundary; it is not a cross-database SQLite
transaction and does not transfer SBE backup-format ownership to this stream.

### I0 — IAM, sharing, and revoke vertical slice

- Workspace add/role-change/remove/rejoin and last-owner/self-demotion rules.
- Page direct and inherited roles: full, edit, database content edit, comment,
  view; groups/teamspaces; internal page-only guest.
- Normal UI invite from existing internal identities only; no external email or
  public link in this slice.
- Atomic operation/audit/outbox and exact idempotent replay.
- Subscription/read/write/search/inbox revoke fencing.

Gate: browser and API permission matrix is exact before and after every change;
outsider oracle is zero; revoke delivers no post-revoke live or unpublished
Notion content and leaves already-published SBE items under SBE policy.

### T0 — Durable event kernel and gateway

- One-time ticket handshake, Origin validation, epoch binding, expiry, rate and
  payload bounds.
- Monotonic page sequence, client operation dedupe, acknowledgement, replay,
  snapshot, compaction, and backpressure.
- Durable commit before broadcast; event/audit/outbox in the content mutation
  transaction.
- Multi-process gateway/service restart and lost-response recovery.

Gate: duplicate, out-of-order, disconnect, restart, and compacted replay all
converge; unauthorized and old-epoch delivery count is zero.

### C0 — Live non-editing collaboration

Implement ephemeral presence and live durable comments, reactions, mentions,
inbox, and page metadata. Presence has heartbeat/TTL and is not a surveillance
history. Durable domain events remain the source of notification delivery.

Gate: two real browsers receive bounded-latency updates without reload;
disconnect expiry, permission revoke, cache, replay, and notification redaction
are correct.

### E1 — Convergent editing

Integrate the E0 winner for rich text, block structure, database cells and
schema-authorized changes. Preserve selection, native Korean IME, local undo,
visible remote cursors, recoverable offline buffer, and exact publication
revision boundaries.

Gate: every client and canonical snapshot ends with the same hash after the
full fault corpus; same-block character loss is zero; content-only actors cannot
change database schema.

### S0/F0 — Integration and adopted feature closure

- S0 closes preview/approve/cancel, attachment/session context, search,
  backlinks, concurrent stale fencing, and current permission behavior.
- F0 closes the U0-selected database views/properties, page history/restore,
  file streaming, capacity UX, and import/export/reminder scope.

Gate: no UI-only fake state; reload and cross-client views derive from canonical
data; immutable independent Second Brain publication identity survives later
Notion edits and Notion revocation without bypassing SBE authorization.

### V0 — Continuous pixel and interaction loop

V0 runs for every visible slice, not only at the end. Freeze Windows/Chrome,
viewport, DPR, zoom, fonts, locale, color scheme, reduced motion, synthetic
content, reference timestamp, and build hash. Require zero unexplained pixels in
deterministic regions; only named masks may cover caret, time, avatar, and
OS-raster noise. Bind geometry, focus, scroll, accessible name/role/state, and
interaction outcome to each capture.

The final build reruns all historical Core scenarios and every new multi-user
state at `1440x900` and `1280x800`.

### O1/Q0 — Recovery and target-class quality

- Produce one coordinated backup generation under an exact write barrier.
- Encrypt outside the live root; verify file hashes, schema, SQLite integrity,
  foreign keys, logical counts, event continuity, and blob references.
- Restore to a new root; never overwrite the only live copy.
- Start with outbound integrations and new mutations disabled, regenerate
  projections, then run owner/member/guest/outsider, realtime reconnect, files,
  history, and Second Brain smoke tests.
- Run target-scale cross-process load, fault, restart, disk-reserve, security,
  keyboard/accessibility, and soak tests. Record p50/p95/p99, SQLite busy count,
  event lag, convergence mismatch, reconnect time, memory, backup age, and
  measured restore time.

### R0/R1 — Promotion and internal pilot

1. Freeze development evidence and source hashes.
2. Back up production.
3. Rehearse migrations and restore on a production-data copy.
4. Deploy additive code/schema with all new capabilities off.
5. Enable a canary cohort of at least two allowed collaborators plus one
   outsider negative control in one named workspace.
6. Exercise aggregate kill switch, capability switches, compatible code
   rollback, and separate disaster recovery.

Development users and content are never promoted. A schema rollback is
forward-compatible code rollback with additive schema retained; backup restore
is a separate DR action into a new root.

## 7. Evidence ladder

Every completion row climbs as high as its risk requires:

1. product contract, ADR, state catalog, permission matrix;
2. static validation and deterministic unit tests;
3. isolated service integration against a unique OS-temp root;
4. real cross-process fault/restart/idempotency tests;
5. multi-principal real-browser journey;
6. reference/local behavior and pixel differential;
7. target-class load, soak, backup, and isolated restore;
8. explicitly approved allowlisted production canary.

User approval records scope or authorizes an external mutation. It does not
replace a test, browser trace, pixel comparison, restore report, or current
source hash.

## 8. Approval packets

Work may continue without additional approval for read-only audits, new plan or
verifier files, isolated synthetic fixtures, default-off code, migration dry
runs, and restore rehearsals wholly below an approved test root.
Automated tests always use sanitized synthetic runner-owned roots; approval never
turns live data or an offline production copy into a pytest fixture. Approved
production-copy, target-host, and canary work is a separate operator action.

The following actions are accumulated as explicit packets:

- `AP-DEV-PAIR`: replace/restart the current 10111 preview with the sanitized
  paired root and start 10112; includes the approved dev identity set.
- `AP-REFERENCE-MULTIUSER`: mutate only synthetic pages/accounts in the chosen
  Notion reference workspace for invite/edit/revoke characterization.
- `AP-OPS-TARGET`: choose backup destination, encryption/key ownership,
  retention, and run a target-host restore drill.
- `AP-PROD-DEPLOY`: production backup, production-copy migration rehearsal,
  default-off deployment, and service restart.
- `AP-PROD-CANARY`: enable the exact cohort/workspace, run soak, kill switch,
  compatible rollback, and DR checks.

## 9. Immediate no-go conditions

Stop the affected slice when:

- personal or non-synthetic content is present in a browser/test root;
- an owned file changes concurrently after its pre-edit hash;
- a protected file or Second Brain contract needs modification without explicit
  ownership transfer;
- a collaboration request still scans user directories or relies on two access
  authorities;
- source/schema version or frozen checksum lineage is ambiguous;
- any revoked/unauthorized content, title, identifier, count, event, or cached
  notification is exposed;
- same-block character loss, IME corruption, or replay divergence occurs;
- a public link or external invitation becomes enabled unintentionally;
- unexplained deterministic pixel differences remain;
- the backup set cannot reconstruct one consistent logical generation;
- production migration, restart, flag, canary, or restore lacks its packet.

## 10. Open interview decisions

Only decisions that materially change architecture or acceptance remain open:

1. **Development identities:** are all identities in the current 10111 registry
   disposable development accounts, or should an allowlisted subset be imported
   into the new paired root?
2. **Scale/topology:** maximum active internal users, maximum simultaneous
   editors on one page, and whether production remains one Windows server for
   the foreseeable first release.
3. **Adopted database/features:** whether first completion requires
   relation/rollup/formula, timeline/chart/dashboard/form/feed/map,
   import/export, reminders, and version restore, or which are deferred and
   hidden rather than left as nonfunctional UI.
4. **Recovery/history:** choose RPO/RTO (`1h/4h` existing baseline or stronger
   `15m/60m`), allowed write-drain duration, backup retention/destination, and
   whether page history may ever be compacted/deleted automatically.
5. **Offline editing:** transient disconnect with in-tab buffered reconnect, or
   durable browser-close/reopen offline authoring.

Until answered, implementation may complete U0 artifacts and isolated
technology probes, but must not freeze scale-dependent schemas, retention,
production SLOs, or a long-term offline model.

## 11. Handoff and concurrent-development discipline

- Re-read `AGENTS.md`, goal status, Second Brain evolution authority, and every
  owned file hash before each slice.
- SBE-adjacent slices first read the ordered 18-document preflight in
  `AGENTS.md`, bind the manifest/current SBE status/schema lineage, and acquire a
  cross-catalog exact-path lease. Approval and file ownership are separate.
- Prefer new unique files. Existing shared files require an explicit owner and
  exact pre-edit hash; drift means stop and rebase, not overwrite.
- A slice owns one cohesive behavior, its tests, evidence, and documentation.
- Run tests serially from the repository root with isolated OS temp roots. A
  Windows `0xC0000409` or empty-output host crash is recorded and retried once;
  it is not converted into pass or code failure without evidence.
- Never use live `knowledge`, `user_data`, `.runtime`, sessions, or per-user
  Notion databases as test targets.
- The repository's current VCS state must be audited before any commit claim.
  Do not stage, revert, or include another developer's files.

## 12. Accurate completion statement

Today the accurate statement is:

> The single-user desktop Core UI has a substantial historical evidence-backed
> foundation. Workspace IAM and offline migration/recovery substrates are
> advanced, but the production page authority, multi-user sharing lifecycle,
> real-time transport, convergent editor, expanded pixel denominator, and
> operational recovery are not yet complete.

The product may be called an internally complete Notion-like collaborative
Second Brain database only when every row in
`internal_collaborative_completion_matrix.v1.json` is satisfied by evidence
bound to the final source, configuration, schema, browser, and deployment
generation.

That closes the Notion product contract only. The active shared goal additionally
requires the then-current SBE `TEST_OPERATIONS.md`, `TRACEABILITY.md`, and
`GOAL_CONTRACT.json` gates; no plan or checkpoint resumes or completes the goal.
