# Internal collaborative Notion + Second Brain scope v2

Status: current product-scope authority for the next implementation phases  
Recorded: 2026-08-23 Asia/Seoul  
Deployment: internal web application  
Primary profile: Chrome desktop, `ko-KR`, controlled company environment

This document supersedes the release priorities in the earlier Core Free UI
release-readiness snapshot. It does not invalidate the 25 already-approved UI
evidence contracts; it expands the completion denominator.

## 1. Product definition

The target is not a clone of Notion's billing, plan-entitlement, marketplace,
or internal SaaS infrastructure. The target is an internal, database-backed
Notion-like workspace that:

1. reproduces the selected Notion web UI and interactions at controlled-pixel
   fidelity;
2. supports workspace and page sharing plus actual simultaneous collaboration;
3. stores pages, blocks, databases, permissions, comments, and history as
   durable application data; and
4. keeps the existing Second Brain publication, attachment, session-context,
   and revision boundaries intact.

“Notion-like” is therefore a claim about the adopted web surface and its
observable behavior, not a claim that every Notion product or plan feature is
implemented.

## 2. Required, deferred, conditional, and excluded scope

### Required now

- Desktop web workspace shell, sidebar, tree, editor, database views, search,
  comments, sharing, and permission-aware empty/error/loading states.
- Workspace members and roles; page-specific guests; groups and teamspaces
  where they participate in inherited access.
- Page access levels equivalent to `Full access`, `Can edit`, database-only
  `Can edit content`, `Can comment`, and `Can view` for the adopted surface.
- Share, role-change, revoke, inherited-access explanation, and immediate
  permission-revocation behavior through the normal UI.
- Real-time presence, simultaneous page editing, live comments/reactions,
  mentions, notification delivery, reconnect, conflict convergence, and
  permission fencing.
- Pixel-exact controlled Chrome comparison for every adopted visible state.
- Coordinated backup, restore, integrity verification, and rollback for the
  Notion data plane and its Second Brain dependencies.
- Internal allowlisted pilot and kill switch. No external product launch is
  implied.
- One feature set for every internal user. There are no Free/Plus/Business/
  Enterprise tiers, seat entitlements, per-user page quotas, workspace block
  quotas, or plan-dependent upload limits.
- Large files use bounded-memory streaming/chunking and operator storage-health
  checks. Physical capacity, security validation, and protocol bounds remain
  fail-closed engineering safeguards, but they are not user plans or product
  quotas.

### Deferred

- Mobile web-specific parity.
- iOS, Android, Windows native, and macOS native applications.
- Offline native application behavior.

### Conditional, only if the company later needs it

- SAML SSO enforcement, SCIM provisioning, domain ownership, DLP/SIEM
  connectors, managed-user controls, and organization-wide content search.
- Additional private-teamspace workflows beyond the adopted internal
  permission model.
- A distinct Archive lifecycle and UI.
- Public web publishing, anonymous collaboration, external email invitations,
  expiring links, and guest-request approval workflows.

### Excluded

- Notion billing, seat charging, trials, plan upsell, entitlements, and sales
  workflows.
- Plan-specific feature flags, file-size tiers, block limits, guest limits, and
  history-retention tiers.
- Notion AI feature parity and private Notion APIs.
- Marketplace, templates marketplace commerce, and native application parity.
- A formal external legal/brand release gate. Proprietary assets and secrets
  still must not be copied into the repository without an explicit license.

## 3. What exists and what does not

### Existing foundation

- Canonical workspace membership, guest, group, teamspace, role, revoke, and
  authorization storage/routes with CAS and audit/outbox coverage.
- Legacy page shares with `view`, `comment`, and `edit` behavior.
- Durable comments, replies, resolve/reopen, reactions, mentions, and inbox
  foundations.
- A visible share inspector and a complete single-user comment journey.
- 25 approved Core UI evidence contracts, including a read-only share-dialog
  differential and a single-user comment-thread differential.
- Second Brain preview/approve/cancel, page attachment/session context, and
  publication/restart regression coverage.
- Default-off pilot control with subject/workspace allowlists and an independent
  kill switch.

### Not yet proven or implemented

- The current share evidence opens and closes the dialog without changing
  access. It does not prove invite, role change, inherited access, revoke, or a
  second user's resulting view.
- The current comment evidence is one-user POST/PATCH/reload behavior. It does
  not prove live delivery to another browser.
- There is no page collaboration transport, presence channel, remote cursor,
  durable edit-event replay, reconnect protocol, or same-block convergence
  proof.
- There is no two-principal, two-browser end-to-end collaboration evidence.
- Current visual differentials certify selected scenarios, not every pixel of
  every adopted state.
- Backup/restore is proven only in an isolated copy-on-write rehearsal, not for
  the named internal deployment data plane.

The old `25/25` result remains valid for its old denominator but no longer means
the newly expanded product scope is complete.

## 4. Collaboration contract

### 4.1 Principals and sharing

The minimum synthetic test set is one owner, one member, and one page guest.
Every operation must distinguish the workspace actor from the page owner.

Required workspace flows:

- add member; change role; remove member; rejoin behavior;
- owner/admin/member authority boundaries;
- group membership and teamspace membership;
- immediate session and subscription fencing after revoke;
- no workspace, page, title, member, or existence oracle for outsiders.

Required page flows:

- share with an existing internal identity;
- choose and change access level;
- show direct versus inherited access;
- revoke direct access and recompute inherited access;
- page/subpage inheritance and explicit override behavior;
- database content-edit permission without schema/view mutation authority;
- guest access limited to explicitly shared pages and descendants;
- public-link sharing default-off unless separately adopted.

Every permission mutation must be idempotent, audited, replay-authorized, and
atomic with the visibility change. A revoked connection must stop receiving
events before the revoke response is considered complete.

### 4.2 Real-time sessions and presence

The preferred transport is an authenticated WebSocket session. A different
transport requires an ADR proving bidirectional ordering, reconnect, and
backpressure behavior. Request-time filesystem scans and source-database opens
remain forbidden.

Each connection is scoped to:

- subject, workspace, page, access epoch, and session ID;
- a bounded heartbeat and presence TTL;
- a last acknowledged durable page-event sequence;
- the current page and visible selection only.

Presence is ephemeral and must not become a surveillance log. It includes
avatar state, viewed block, cursor/selection, and optional viewport anchor.
Disconnect or heartbeat expiry removes it. Presence never grants access.

### 4.3 Simultaneous editing

The implementation must not send whole-page last-writer-wins saves as the
real-time protocol. It needs durable, idempotent operations with a per-page
monotonic sequence and a client operation ID.

- Structural block insert/move/delete operations use server-authoritative
  ordering and deterministic transforms.
- Rich-text changes use a reviewed CRDT or equivalent convergence algorithm;
  do not invent an unproven merge algorithm inside route code.
- Block attributes and database property edits define deterministic conflict
  rules and never silently mutate database schema under content-only access.
- Accepted operations commit with the page revision and durable event/outbox in
  one transaction.
- Clients acknowledge sequence numbers, deduplicate operation IDs, replay from
  the last acknowledged sequence, or receive a bounded canonical snapshot when
  the event window has compacted.
- Reconnect, duplicate delivery, out-of-order delivery, response loss, and
  server restart must converge to the same canonical document.

Required two-browser assertions:

- edits appear without reload;
- simultaneous different-block edits both survive;
- simultaneous same-block text edits converge without character loss;
- move/delete versus edit has one documented deterministic outcome;
- offline edit/reconnect converges or presents a visible recoverable conflict;
- stale/revoked users cannot publish an edit or continue receiving content;
- comments, reactions, mentions, and resolved state propagate live;
- focus, selection, Korean IME composition, and undo/redo remain locally sane.

### 4.4 Comments, mentions, and inbox

Comments remain durable domain data. Real-time delivery is an effect of the
same committed event, not an independent write. Mentions create permission-
filtered inbox events. A user who loses page access must not retain comment
content through inbox, reconnect, notification payload, or cached event replay.

## 5. Pixel-exact UI contract

The target is controlled-environment pixel equality, not a vague visual
similarity score.

- Freeze Chrome version, Windows version, viewport, DPR, zoom, font files,
  locale, color scheme, reduced-motion setting, and synthetic content.
- Capture reference and local states at identical viewport and interaction
  boundaries.
- Require zero unexpected differing pixels for deterministic regions.
- Dynamic timestamps, caret blink, avatars, and OS-controlled raster noise may
  be masked only by named, reviewed masks; a global tolerance must not hide
  layout or styling differences.
- Bind screenshots, DOM-independent geometry, focus owner, scroll position,
  accessible name/role, and interaction result to the evidence record.
- Cover idle, hover, active, focus-visible, disabled, loading, empty, error,
  menu, dialog, peek, sharing, collaboration, and permission-denied states.
- Repeat at `1440x900` and `1280x800`; narrow web remains a deferred responsive
  check rather than native/mobile parity.

“Pixel complete” is reached only when every adopted state has a hash-bound
reference/local comparison and no unexplained diff. Scenario-level green tests
alone do not satisfy this gate.

## 6. Second Brain invariants

Collaboration must not weaken the current integration boundary.

- A Notion page revision and a Second Brain publication revision remain
  distinct immutable identities.
- Preview observes one committed Notion revision; approve publishes exactly
  that revision or fails stale. Concurrent edits never change an already
  approved snapshot.
- Cancel creates no Second Brain item or revision.
- Attach/detach and session context recheck the current page permission.
- Page/member/share revocation removes future Second Brain visibility without
  deleting immutable audit evidence.
- Search and backlinks filter by the current principal before returning titles,
  snippets, identifiers, or counts.
- Event payloads contain no raw token, source path, filename, or unauthorized
  page content.

## 7. Operational backup and restore

The protected data plane includes the workspace catalog, legacy Notion shards,
Second Brain canonical database, blobs, projections, migration markers, and the
minimum configuration required to interpret them. Sessions, cookies, and raw
tokens are not restored as content.

Required implementation:

1. inventory exact files, schema versions, hashes, sidecars, and ownership;
2. take one coordinated read-consistent backup without pathname races;
3. encrypt and retain backups outside the live data root;
4. verify raw hashes plus SQLite integrity, foreign keys, schema contracts, and
   logical counts;
5. restore into a new isolated root first—never overwrite the only live copy;
6. start the application against the restored root with outbound integrations
   disabled;
7. run owner/member/guest permissions, page/database round-trip, real-time
   reconnect, and Second Brain publication/attachment smoke tests;
8. record measured RPO/RTO, then promote or roll back explicitly.

Initial internal targets are RPO at most 15 minutes and RTO at most 60 minutes.
They are engineering targets to confirm with the operator, not claims about the
current implementation.

## 8. Internal capability and environment policy

Notion pricing tiers are reference-account constraints only and are never part
of this product's domain model. Every active internal account receives the same
adopted editor, database, collaboration, sharing, history, and file features.
Adding a capability is a product decision, not a plan upgrade.

There is no logical file-size quota. The current host still has fixed Flask
request ceilings (`50 MiB` on the Agent host and `100 MiB` on the Notion host),
so “unlimited internal use” requires replacing buffered uploads with streaming
or chunked storage before the claim is made. Uploads must reserve capacity and
fail before disk exhaustion. The UI reports operational capacity failures; it
never presents a plan-upgrade message.

Archive remains a possible workflow, not a paid tier. If adopted, archived
content remains addressable, keeps links/history, is hidden from normal
search/database views by default, archives descendants, and can be unarchived.
It remains lower priority than real-time collaboration, sharing, pixel parity,
and backup.

### 8.1 Production pair

- `10011`: Second Brain Agent.
- `10012`: Notion workspace.
- Both use the same production `CODE_ASSIST_DATA_ROOT`, auth users file, auth
  secret, host, cookie namespace, and account identities.
- Signing in at `10011` therefore authenticates `10012`; no duplicate Notion
  account is created.

### 8.2 Development pair

- `10111`: existing isolated Second Brain development/preview host and its
  temporary internal users.
- `10112`: new Notion development host.
- Both must use one exact marker-bound development `CODE_ASSIST_DATA_ROOT`, the
  same development users file and secret, and a development-only shared cookie
  name.
- `10112` must not open, migrate, copy back to, or create accounts in the
  `10012` production root.
- Development content is synthetic. Any pre-existing copied content under the
  `10111` preview root must be inventoried before use and excluded from browser
  evidence if it is personal.

Browser cookies are scoped by host rather than port. The current applications
hardcode the cookie name `session`, so merely changing `10011/10012` to
`10111/10112` is not isolation. The development pair needs a distinct exact
cookie namespace shared by `10111` and `10112`, while the production pair keeps
its own namespace. Both hosts must also use strict configurable peer origins;
current static links hardcode `10011` and `10012` and would otherwise escape
from development into production.

### 8.3 Promotion

Promotion moves reviewed code, schema migrations, static assets, and explicit
configuration—not development users or development page content.

1. freeze development evidence and source hashes;
2. back up the production data plane;
3. apply migrations to a production-data copy and run the full suite;
4. deploy code with new capabilities default-off;
5. enable one named production user/workspace canary;
6. verify permissions, collaboration, pixels, backup, and Second Brain;
7. promote or exercise the kill switch and roll back.

## 9. Implementation phases and gates

### D0 — Paired development-host isolation

- Add a validated environment-controlled session-cookie name shared only by
  the paired hosts.
- Add strict loopback peer-origin configuration so `10111` links to `10112`
  and `10112` links to `10111` without changing production defaults.
- Launch `10112` against the already marked `10111` development root and reuse
  its temporary users; do not seed users in `10012`.
- Prove development and production cookies, data roots, CSRF sessions, and
  links cannot cross.
- Keep all collaboration flags default-off.

Gate: login at `10111` opens `10112` as the same development user; a second
development user gets an independent session; `10011/10012` cookies and data
are unchanged; every cross-environment attempt fails closed.

### C0 — Two-principal harness and collaboration contract

- Add isolated owner/member/guest fixtures and two independent browser
  contexts.
- Freeze the permission matrix, collaboration messages, ordering, redaction,
  reconnect, and revocation contracts.
- Record current share/comment behavior as baseline, not as real-time proof.

Gate: two browsers authenticate independently; no live/personal data; no
cross-principal cache/session bleed; deterministic synthetic evidence.

### C1 — Sharing and permission lifecycle UI

- Complete workspace people, page share, roles, inherited access, revoke, and
  guest journeys through the normal UI.
- Keep public link off.

Gate: owner/member/guest see exactly the allowed tree/page/database/comment
surface before and immediately after each mutation; all negative oracles close.

### C2 — Durable collaboration event foundation

- Add exact schema/migration for page event sequence, operation dedupe,
  compaction boundary, and subscriber fencing only after design review.
- Implement authenticated transport handshake, replay cursor, heartbeat,
  backpressure, and restart recovery.

Gate: committed events survive restart and duplicate/out-of-order delivery;
revocation terminates delivery; no content leak.

### C3 — Presence and live asynchronous collaboration

- Presence, live comments, reactions, mentions, inbox, and page metadata.

Gate: two real browsers observe bounded-latency updates and correct TTL/revoke
behavior without reload.

### C4 — Convergent simultaneous editing

- Structural operations, rich-text convergence, database-cell edits,
  selection/cursor rendering, undo/redo, Korean IME, offline/reconnect.

Gate: deterministic multi-client convergence corpus plus crash/reconnect and
permission-race tests; no silent character loss.

### C5 — Full pixel-parity expansion

- Capture every adopted state, including multi-user avatars/cursors, share
  roles, comments, conflicts, and permission denial.

Gate: zero unexplained deterministic-region pixel differences at both desktop
viewports and exact focus/interaction parity.

### C6 — Coordinated operational recovery

- Implement the backup pipeline and restore to a new isolated root; then run a
  named internal deployment rehearsal when explicitly authorized.

Gate: measured RPO/RTO, verified restored hashes and permissions, Second Brain
smokes green, rollback retained.

### C7 — Internal pilot

- Enable only for named internal subjects/workspaces after C1-C6.
- Monitor edit convergence, permission failures, event lag, and backup health;
  exercise the independent kill switch.

Gate: no P0/P1, no unauthorized visibility, rollback tested. This is the
internal completion gate; an external legal/brand launch review is not part of
the product definition.

Dependency order: `D0 -> C0 -> C1 -> C2 -> C3 -> C4 -> C5 -> C6 -> C7`.
Second Brain regression is a hard gate across every phase, not a final add-on.

## 10. Revised completion statement

The product may be described as an internal Notion-like collaborative Second
Brain database only when all of the following are true:

- adopted single-user Core UI scenarios remain green;
- every internal account receives the same adopted capability set without plan
  quotas, and large files use bounded-memory operational storage controls;
- the `10111/10112` development pair is isolated from `10011/10012`, while each
  pair shares its own account session correctly;
- sharing and permissions are proven with owner/member/guest browsers;
- simultaneous editing, presence, comments, mentions, reconnect, and revoke
  converge in real time;
- every adopted state meets the controlled-pixel contract;
- Second Brain permission and immutable-publication invariants remain green;
- operational backup/restore meets the agreed RPO/RTO; and
- one allowlisted internal pilot completes with the kill switch tested.

Until then, the accurate claim is: “the single-user Core UI foundation is
Notion-like and evidence-backed; multi-user collaboration, full pixel parity,
and operational recovery are active required work.”

## Official reference basis

- Notion collaboration: https://www.notion.com/help/collaborate-within-a-workspace
- Sharing and permissions: https://www.notion.com/help/sharing-and-permissions
- Members, admins, and guests: https://www.notion.com/help/add-members-admins-guests-and-groups
- Comments, mentions, and reactions: https://www.notion.com/help/comments-mentions-and-reminders
- Backup and restore: https://www.notion.com/help/back-up-your-data
- Delete, Trash, history, and Archive: https://www.notion.com/help/duplicate-delete-and-restore-content
- Archive beta: https://www.notion.com/help/archive-pages
- Current plan comparison: https://www.notion.com/pricing
