# Internal collaborative Notion — xhigh implementation handoff v1

Status: superseded by `INTERNAL_COLLABORATIVE_NOTION_XHIGH_IMPLEMENTATION_HANDOFF_V2.md`  
Recorded: 2026-08-23 Asia/Seoul  
Parent plan: `INTERNAL_COLLABORATIVE_NOTION_ULTRA_EXECUTION_PLAN_V3.md`  
Product completion rows: `internal_collaborative_completion_matrix.v1.json`  
Packet catalog: `internal_collaborative_packets.v2.json`  
Validation catalog: `internal_collaborative_validation_ladder.v1.json`

This handoff refines the Ultra V3 execution order. It does not authorize any
production action and does not reinterpret the current 25 single-user scenarios
as collaborative completion evidence.

Do not execute this version. Its corrected successor fixes packet ordering,
decision ownership, matrix closure, kill-switch timing, visual execution,
recovery/deployment separation, and final completion gating.

## 1. Resume point

The next agent must continue from these facts rather than restart discovery:

- the user wants an internal collaborative Notion-like database integrated with
  Second Brain, not Notion billing, plan entitlement, mobile, or native apps;
- all active internal accounts receive the same adopted capabilities;
- `10011/10012` is the production pair and `10111/10112` is the intended
  development pair;
- a Second Brain login supplies the same subject to the paired Notion host;
- the existing 10111 preview root is not synthetic-only because its launcher can
  copy `knowledge`, `user_data`, sessions, workspaces, and the auth registry;
- development identities may be reused, but the existing data root, secret,
  session, audit, and content must not be copied into the collaboration fixture;
- workspace schema `2026081317` is an advanced IAM/locator/tree/lifecycle
  substrate, but it is not a canonical collaborative page/block/comment/event
  store and its runtime resolver is not cut over;
- legacy page saving is whole-page revision CAS and cannot prove same-block
  convergence;
- current production hosting is Flask/Waitress and has no approved WebSocket,
  ASGI, Node-service, or CRDT dependency;
- existing UI differentials do not prove exact RGBA pixel equality, and existing
  browser journeys are single-principal;
- goal state is runtime state, not a fact frozen by this handoff: re-query it at
  session start and never replace or complete it merely because a packet ends;
- the product completion matrix now has 28 rows: the original 23 plus explicit
  search, collaborative accessibility, collaboration security, observability,
  and uniform internal-capability closure rows.

## 2. Corrected executable dependency graph

The Ultra phases are split at the points where user decisions or technical
evidence are genuinely required.

```text
U0a current inventory, capability draft, phase/evidence harness
  ├─ D0a synthetic paired-root/config/launcher tests
  ├─ H0a generated multi-principal browser harness
  ├─ A0a canonical authority and transaction ADR/prototype
  ├─ E0 CRDT + gateway/runtime candidate spike
  ├─ T0a ticket/envelope/sequence/backpressure protocol spike
  └─ O0a synthetic consistency-barrier/restore benchmark

U0a + D0a + H0a + A0a + E0 + T0a + O0a
  └─ G0 user decisions and engineering winner freeze

G0
  ├─ D0b approved development identity import and real 10111/10112 switch
  ├─ A0b forward schema/version/import/cutover freeze
  └─ T0b selected CRDT update/snapshot/compaction binding

D0b + A0b + H0a
  └─ I0 workspace/page IAM, share, inherited access, immediate revoke

I0 + T0b
  └─ C0 live presence/comments/reactions/mentions/inbox

I0 + T0b + E0
  └─ E1 convergent rich-text/block/database editor

E1 + I0
  ├─ S0 exact-revision Second Brain integration
  └─ F0 adopted databases/files/history/import/export/reminder surface

Every visible packet ──> V0 interaction + accessibility + exact pixel evidence

A0b + E1 + S0 + F0 + O0a
  └─ O1 coordinated backup and isolated restore

I0 + C0 + E1 + S0 + F0 + V0 + O1
  └─ Q0 target load/security/fault/soak gate
       └─ R0 approved production-copy rehearsal
            └─ R1 approved multi-user production canary
```

`A0b` must not allocate or freeze physical CRDT columns before E0 chooses an
update/snapshot format. `T0a` may define ticketing, envelope, sequence, ACK,
replay, and bounds before E0, but `T0b` binds the chosen CRDT payload only after
E0 passes.

## 3. Work allowed before interview answers

The following packets make real progress without touching an existing dev or
production account:

- `U0A-P1`: current authority/route/capability inventory;
- `U0A-P2`: phase gate, validation, evidence, and completion verifier contracts;
- `D0A-P1`: pure cookie/origin/pair configuration parser and negative tests;
- `D0A-P2`: new marker-bound paired-root builder with four generated identities;
- `D0A-P3`: isolated 10111/10112 launcher/readiness tests without starting the
  user's current services;
- `H0A-P1`: owner/member/internal-page-guest/outsider fixtures;
- `H0A-P2`: two-or-more ephemeral browser contexts and leak oracle;
- `A0A-P1`: current route/storage authority map and one-authority ADR;
- `A0A-P2`: unversioned, disposable SQLite transaction prototype;
- `E0-P1..P4`: candidate matrix, convergence corpus, runtime spike, winner ADR;
- `T0A-P1..P3`: one-time ticket threat model and transport-only prototype;
- `O0A-P1..P3`: backup consistency and compaction design on synthetic data;
- `V0A-P1`: exact RGBA comparator and explicit mask verifier.

No actual 10111 account registry, service, or root is used by these packets.

## 4. G0 decision freeze

G0 is the first mandatory stop. It records the user's answers and the E0/A0a
technical evidence. It freezes:

1. approved development identity IDs or confirmation that the registry is fully
   disposable;
2. deployment topology and scale envelope;
3. adopted/deferred/hidden feature denominator;
4. RPO, RTO, write-drain, destination, key owner, backup retention, and page
   history retention;
5. transient in-tab or durable browser-close offline behavior;
6. canonical authority architecture and selected gateway/CRDT runtime;
7. exact next schema version after re-reading the then-current frozen lineage.

The following provisional values may drive spikes but are not release claims:

- one Windows server;
- 50 simultaneous connections and 10 simultaneous editors on one page;
- transient in-tab disconnect buffering only;
- benchmark target RPO 15 minutes, RTO 60 minutes, write drain at most 60
  seconds;
- user-visible page revisions are not automatically deleted;
- required candidate features: historical Core 25, workspace/page sharing,
  comments/mentions/inbox, history/restore, files, table/board/list/gallery/
  calendar/timeline, filter/sort/group, relation/rollup/formula, templates,
  Markdown/CSV import/export, and reminders;
- hidden/deferred candidate features: chart/dashboard/form/automation/feed/map,
  public link, external email guest, and AI.

## 5. Architecture handoff

### 5.1 D0 pair boundary

Use a new external, empty, non-reparse, marker-bound root. The default identity
import allowlist is empty. A later approved import copies only the minimum
registry fields and password hash for approved disposable accounts. It never
copies the prior secret, sessions, audit, content, workspace, blobs,
projections, or tokens.

Development defaults:

- canonical host: `127.0.0.1` only;
- Agent origin: `http://127.0.0.1:10111`;
- Notion origin: `http://127.0.0.1:10112`;
- reserved collaboration gateway origin: `ws://127.0.0.1:10113` until E0/G0;
- dev-only cookie name distinct from production and shared by 10111/10112;
- one new dev signing secret shared by 10111/10112;
- 10111 is account-mutation authority; 10112 signup/account mutation is denied;
- missing/invalid pair config disables the peer link and collaboration ticket;
  it never falls back to ports 10011/10012.

Peer navigation configuration and unsafe-request Origin/CSRF validation are
separate. Cross-port links are allowed only as exact navigation targets; a
10111 Origin does not authorize a 10112 mutation.

### 5.2 Canonical content authority

The provisional preferred design is to extend the same physical workspace
SQLite authority that owns workspace IAM. One transaction should commit:

- current workspace membership and page role check;
- workspace and page access epochs;
- operation ID, canonical request hash, and replay result;
- canonical document/block/database/comment mutation;
- per-document monotonic event sequence;
- user-visible revision or checkpoint identity when appropriate;
- audit, outbox, and durable collaboration event.

Large CRDT snapshots and attachments may be content-addressed blobs, but the DB
owns their hash, size, state, reference, and publication decision. Legacy owner
shards become immutable import sources or generated compatibility projections;
they are never a second write authority.

A0a must document a fallback owner-shard/single-coordinator option, but may
select it only with proof of cross-database permission fencing, immediate
revoke, restart recovery, no directory scans, and single-writer deployment.

### 5.3 Gateway and CRDT

Freeze a dedicated WebSocket boundary, not an implementation language. E0 must
compare at least:

- Python ASGI plus a Windows/Python 3.14-compatible CRDT binding;
- Python ASGI as an opaque relay plus a separately verified compactor;
- Node/Yjs-native gateway plus the canonical Python command boundary.

The gateway does not trust the Flask session cookie directly. The Notion host
issues a short-lived, one-time, CSRF-protected ticket bound to subject,
workspace, page, client/session, capabilities, workspace/page access epochs,
JTI, issued time, and expiry. Only a digest is retained for one-time
consumption.

The selected editor should use a mature CRDT for rich-text leaves and
server-sequenced typed commands for the block tree and database schema. An
invented route-level OT/merge implementation is out of scope.

### 5.4 Sequence, revision, and history

Keep these identities distinct:

- client operation ID: retry/dedup identity;
- event sequence: total order within one canonical document stream;
- CRDT/update generation: protocol-format identity;
- checkpoint/snapshot ID: replay-compaction boundary;
- document revision: user-visible committed content milestone;
- Second Brain publication revision: immutable external publication identity.

Compaction may remove operations only after a verified snapshot, event
high-water mark, backup generation, replay proof, and retention rule. It never
invalidates an immutable publication revision.

### 5.5 Revocation

Every read, mutation, replay, snapshot, search result, inbox row, and outbound
event is bound to the current access epoch. Delivery revalidates immediately
before send. A revoke response is not complete while an old-epoch write,
replay, notification, queued event, or subscription can still succeed.

### 5.6 Second Brain

Second Brain is a protected dependency and a hard regression in every phase.
The Notion side may add an adapter that captures a canonical document revision
and access epoch. It does not edit `knowledge_core/**`, Second Brain evolution
contracts, or `app_code.py` without an explicit ownership transfer.

## 6. File ownership lanes

Use exact pre-edit SHA-256 locks because the current path is not recognized as a
Git repository. Never infer ownership from file mtime alone.

### Lane RUNTIME

Likely shared files:

- `app_code.py` — protected; explicit transfer required;
- `app_notion.py`;
- auth/session composition;
- Agent and Notion static peer-link consumers.

Own only for D0, then release before R0/R1.

### Lane CANONICAL

- `notion_workspace_store.py` and new forward migrations;
- workspace/collaboration routes and route manifests;
- canonical resolver/import/cutover modules.

Only one writer agent owns `notion_workspace_store.py`. Prefer new modules and
thin integration seams. Re-read the latest schema version before every
migration packet.

### Lane UI

- `notion_static/notion.js`, `notion.css`, `index.html`;
- editor core and new collaboration UI modules.

Only one writer agent owns these files. First add a narrow host-adapter API as a
behavior-preserving packet so later collaboration modules do not reach private
global state directly. Rerun all six existing Node contracts and the historical
browser journey after that refactor.

### Lane SECOND-BRAIN/PRODUCTION

- `app_code.py`, `knowledge_core/**`, `docs/knowledge_spaces/**`;
- production config, service composition, backup, health, and rollout files.

These remain read dependencies through S0/O1. Any necessary modification is a
separate ownership packet with Second Brain evolution regression.

## 7. Slice execution harness

Each packet follows this exact order:

1. re-read `AGENTS.md`, goal status, parent handoff, packet catalog, and relevant
   current contracts;
2. record owned/read/protected file hashes and current schema/dependency/protocol
   versions;
3. run the packet's pre-edit baseline lane;
4. make one cohesive default-off change;
5. run focused unit tests;
6. run adjacent regression;
7. run service integration;
8. run real cross-process/fault tests when required;
9. run Node and Playwright lanes for visible behavior;
10. run the phase audit verifier;
11. record a hash-bound checkpoint and `STATUS_DELTA`; shared status files are
    changed only by their current owner;
12. release ownership before another lane touches the same shared file.

The runner has two modes:

- `audit`: verifies that documents, packets, evidence, and incomplete status are
  honest and internally consistent;
- `gate`: exits non-zero unless every required phase or product row has strong
  evidence.

An audit command that truthfully reports incomplete work is never used as a
completion gate.

## 8. Evidence identity correction

Dev, reference, load, restore, and production realms cannot have one identical
configuration fingerprint. Evidence instead binds:

- one `release_candidate_id` derived from the source manifest, dependency
  locks, schema catalog, protocol catalog, and static assets;
- one approved redacted `configuration_fingerprint` per realm;
- proof that each local/load/restore/production configuration derives from the
  same configuration contract;
- a separate Notion reference environment/build fingerprint per scenario;
- the final canary `deployment_generation_id`.

Every receipt records exact argv, environment class, start/end, exit code,
counts, JUnit/canonical receipt hashes, source pre/post hashes, retry status,
process cleanup, and evidence artifact hashes. It never records raw cookies,
tokens, passwords, personal names, page titles, or absolute private paths.

## 9. Pixel verifier correction

The existing semantic differential rejects `pixel_identity_required=true` and
cannot prove PIX-001. Add a separate synthetic-only exact comparator that:

- requires equal viewport, DPR, dimensions, PNG decode mode, and color space;
- compares exact RGBA in deterministic regions;
- accepts only named rectangular/polygon masks bound to scenario, state,
  reference hash, reviewer, and reason;
- rejects overlapping catch-all masks, global tolerance, size mismatch, alpha
  mismatch, unknown dynamic regions, and unredacted/private captures;
- emits differing-pixel count, bounding boxes, diff image hash, mask hash, and
  geometry/focus/accessibility receipt;
- is tested against tampered image, mask, metadata, and hash fixtures.

Run it for every visible I0/C0/E1/S0/F0 slice and rerun the entire adopted state
catalog at `1440x900` and `1280x800` on the final candidate.

## 10. Windows and test isolation

- Run pytest from the repository root and let the existing `conftest.py` create
  a unique trusted OS-temp root. Do not supply a workspace-local `--basetemp`.
- Node, gateway, CRDT, browser, and child processes inherit the guarded root and
  scrubbed secret environment.
- Browser automation uses fresh contexts, never the user's persistent Chrome
  profile.
- Raw HAR, trace, DOM dumps, cookies, and private screenshots stay outside the
  repository and are redacted before an approved hash-only receipt is created.
- Kill only runner-owned PIDs or a runner-owned Windows Job Object. Never kill
  by broad process name.

For Windows exit `-1073740791` (`0xC0000409`), timeout, or child disappearance:

1. preserve the original result;
2. verify runner-owned process and port cleanup;
3. retry once in a fresh process and fresh OS-temp root;
4. a passing retry marks the lane `host_flaky`, not pass;
5. require two later consecutive clean runs before phase closure;
6. a second host crash is `infrastructure_blocked`; an assertion failure is a
   normal code failure and is not retried.

## 11. Approval ledger

Every packet contains exact environment/process/port/root/subject/workspace,
precondition hashes, commands, external mutations, forbidden scope, success
evidence, rollback, expiry, and a hash-drift invalidation rule.

- `AP-DEV-PAIR`: approved development identity allowlist, new root, 10111
  replacement/restart, and 10112 start.
- `AP-RUNTIME-SPIKE`: download/install exact CRDT/ASGI/Node candidates and create
  a reviewed dependency lock, if not already locally available.
- `AP-REFERENCE-MULTIUSER`: invite/edit/revoke/cleanup only on synthetic Notion
  reference pages and accounts.
- `AP-OPS-TARGET`: backup destination, encryption/key owner, retention, and
  target-host isolated restore drill.
- `AP-PROD-DEPLOY`: production backup, data-copy rehearsal, default-off
  deployment, gateway service installation, and restart.
- `AP-PROD-CANARY`: exact two-or-more collaborators, outsider control,
  workspace, duration, abort threshold, kill switch, and compatible rollback.
- `AP-PROD-RESTORE`: incident-specific live restore only; never implied by a
  rehearsal or canary approval.

## 12. Immediate packet order

The next implementation session can proceed without further user input in this
order:

1. `U0A-P2` — implement packet/phase/validation audit verifiers and tamper tests;
2. `D0A-P1` — pure paired runtime configuration contract;
3. `D0A-P2` — empty-root/generated-identity builder;
4. `H0A-P1` — generated four-principal fixture;
5. `A0A-P1` — authority map and transaction ADR;
6. `E0-P1/E0-P2` — candidate matrix and convergence corpus without installing
   a new production dependency;
7. `T0A-P1` — ticket/envelope/sequence protocol contract;
8. `O0A-P1` — consistency-barrier and compaction ADR;
9. `V0A-P1` — exact pixel comparator contract.

Stop at the first dependency download or real-account/root/service mutation and
assemble the corresponding approval packet rather than expanding authority.

## 13. Accurate handoff claim

This handoff makes the work executable; it does not make the product complete.
The first implementation milestone is not "realtime works." It is:

> The validation harness, synthetic paired environment, multi-principal
> fixture, canonical authority ADR, convergence corpus, transport protocol, and
> backup-consistency design are all reproducible and tamper-checked without
> touching the user's current development or production state.

Only after G0 and `AP-DEV-PAIR` may the implementation replace the current
10111 preview and start the real 10112 development pair.
