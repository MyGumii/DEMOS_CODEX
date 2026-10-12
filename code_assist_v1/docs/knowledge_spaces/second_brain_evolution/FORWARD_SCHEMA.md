# Forward schema contract (canonical v8-v13)

Status: logical schema registered. `SBE-0.1` froze the exact logical DDL/API
contract, primary filenames, source-ID/checksum/fingerprint protocol, and table
rebuild order before implementation. Because v8-v13 sources do not exist at that
gate, each owning schema packet freezes and registers its actual source SHA-256,
migration checksum, physical fingerprints, and catalog/upgrade-path hashes in
the same reviewed integrator commit that introduces the implementation.

This document describes forward-only successors to the verified canonical-v7
catalog. It never authorizes editing v1-v7 migration sources or
`SCHEMA_V1.md`. SQLite table rebuilds must use new migrations, a deployment-only
migrator, exact postconditions, and rollback-on-failure semantics.

## 1. Version map

| Version | Migration purpose | Owned packets |
|---|---|---|
| v8 | Multiple personal Wikis, default/archive lifecycle, pagination indexes | `SBE-1.1`, `SBE-2.1`, `SBE-2.2` |
| v9 | Native Team/Responsibility writes, leadership episodes, explicit distribution | `SBE-1.2`, `SBE-2.3` |
| v10 | Aggregate graph, semantic-auto intent/origin/evidence/suppression, generation vectors | `SBE-1.3`, `SBE-4.1`, `SBE-4.2` |
| v11 | Explicit higher-native publication preview/operation authority and reviewed-curation compatibility | `SBE-2.3` |
| v12 | Revision temporal assertions, verification attestations, projection manifest v2 | `SBE-3.2`, `SBE-5.2` |
| v13 | One direct organization-home affiliation per user; Part membership compatibility mirror; direct Team-leader and Responsibility-executive homes | `SBE-8.8` |

The exact primary source filenames are
`v008_personal_wiki_profiles.py`, `v009_higher_native_spaces.py`,
`v010_aggregate_semantic_graph.py`, and
`v011_higher_publication_authority.py`, and
`v012_temporal_projection_footer.py`, and
`v013_direct_org_affiliation.py`. A packet may add same-version helper
modules only under the primary module's atomic catalog entry; it cannot create a
second independently committed version. SBE-0.1 reserves the filename/version/
owned-packet tuple, and the owning schema packet registers the real source
SHA-256, physical/pristine/upgraded fingerprint, migration checksum, catalog
hash, upgrade-path hashes, and rollback/no-op evidence in one integrator commit.
Never edit v1-v7 sources.

## 2. Migration invariants

- Apply only from an exact reviewed canonical-v7-or-later fingerprint.
- Acquire the existing deployment migrator lock; ordinary app opens never
  migrate.
- Run `foreign_key_check`, integrity checks, exact table/index/trigger SQL
  fingerprint checks, and semantic postconditions before committing.
- A failure restores the exact prior version with no helper object or partial
  ledger residue.
- Migrations are append-only. Existing completed migrations and historical
  evidence hashes do not change.
- Unknown pre-existing object names, duplicate semantic rows, invalid role or
  default state, and noncanonical identifiers fail closed.
- Schema-created timestamps are deterministic DB values only where they are
  domain events. Migration execution time never becomes a document's content
  modified/effective time.

## 3. v8 — personal Wiki collection

### 3.1 `personal_wiki_profile`

One row exists for every `knowledge_space(space_kind='personal')`.

Planned columns:

| Column | Contract |
|---|---|
| `space_id` | primary/foreign key to personal `knowledge_space`, stable UUID |
| `owner_user_id` | exact same owner as `knowledge_space.owner_user_id` |
| `display_name` | bounded trimmed user-facing spelling stored without NFKC/casefold rewriting; reject control/format/private/unassigned characters |
| `name_key` | deterministic NFKC+casefold of `display_name`, computed/validated by service and never rendered |
| `lifecycle_status` | `active|archived` |
| `lifecycle_generation` | positive monotonic integer; starts at 1 and increments only on archive/restore |
| `is_default` | strict boolean; true only for active Wiki |
| `archived_at` | null for active; UTC ms for archived |
| `archived_by_user_id` | null for active; exact owner only for archived |
| `archive_reason` | optional bounded nonblank owner note; null is allowed |
| `created_at`, `updated_at`, `row_version` | UTC ms and monotonic CAS fields |

Required constraints/indexes:

- unique `(owner_user_id, name_key)` across active and archived profiles; an
  archived name remains reserved so restore cannot collide;
- partial unique active-default index on `owner_user_id` where
  `lifecycle_status='active' AND is_default=1`;
- owner/status/name/cursor index for active listing;
- archived-management cursor index;
- triggers reject owner mismatch, archived default, illegal lifecycle tuples,
  lifecycle-status changes without exactly `old+1` generation, generation changes
  without a lifecycle-status change, and direct deletion.

Exactly one active default is stronger than the partial index's "at most one".
Create, set-default, archive, and restore services must verify the exact-one
postcondition in the same `BEGIN IMMEDIATE` transaction. The last active Wiki
and current default cannot be archived.

### 3.2 Existing personal-space uniqueness

The v7 partial unique index that permits one personal `knowledge_space` per
owner must be replaced by forward migration. The `knowledge_space` primary key,
owner, items, conversations, graph, and projections are preserved.

Backfill rule:

1. verify each v7 user has exactly one valid personal space;
2. insert one profile with a deterministic localized default name and
   `is_default=1` using the existing `space_id`;
3. prove no canonical item/revision/conversation ID or body hash changed;
4. replace the owner-only unique index with the profile lifecycle indexes;
5. prove every personal space has exactly one profile and every owner exactly
   one active default.

There is deliberately no count-limit column, trigger, or index.

### 3.3 Conversation binding

`conversation.space_id` remains the authoritative Wiki binding. No mutable
"current Wiki" is stored on the conversation and no browser cache becomes
canonical.

Service constraints added at create/continue time:

- new conversations require an active authorized Wiki/view;
- an archived personal Wiki permits history reads but not a new turn;
- continue-after-restore uses the same conversation and space ID after current
  authorization/epoch validation;
- selector changes create a new conversation.

### 3.4 Pagination and global personal search

Indexes must support cursor pagination by a stable tuple such as
`(name_key, space_id)` and recent order by `(updated_at, space_id)`. Cursors are
opaque, versioned, scope/owner-bound, and reject tampering/stale filter reuse.

Global personal search must join/filter by owner and active profile in one
bounded SQL/FTS plan. APIs may not materialize a caller-sized `IN (...)` list or
issue one query per Wiki.

## 4. v9 — higher native spaces and leadership

### 4.1 `knowledge_space` forward rebuild

The v7 check that forces Team/Responsibility `write_enabled=0` is removed only
by a new table rebuild. All other ID, ownership, generation, status, and foreign
key invariants remain.

New checks/postconditions:

- Part, Team, and Responsibility spaces have an owner org unit of exactly the
  matching kind;
- after the v9 migration commits, exactly one native organization space exists
  for every active Part, Team, and Responsibility org unit. Pre-v9 current-v7
  state is a migration input, not a permitted v9 postcondition; rollout flags
  control read/write exposure rather than whether the canonical space exists;
- `write_enabled=1` requires active space status and an exact open role episode
  appropriate to that kind (Part remains governed by its existing leader
  contract);
- role closure/disable and write-disable are atomic;
- personal spaces cannot be owned by an organization and organization spaces
  cannot be owned by a user.

### 4.2 `higher_scope_role_assignment`

The existing v1 `part_leader` history remains unchanged. A new append-only
episode table avoids widening an immutable CHECK in place.

Planned columns:

| Column | Contract |
|---|---|
| `id` | UUID episode ID used for ABA fencing |
| `org_unit_id` | Team or Responsibility unit |
| `user_id` | active canonical user |
| `role` | `team_leader|responsibility_executive` |
| `assigned_by_user_id` | authorized administrator |
| `assigned_at`, `ended_at` | UTC ms; exactly one open/closed tuple |
| `end_reason` | required on close |
| `assignment_generation` | monotonic per unit/role |
| `created_at`, `updated_at`, `row_version` | immutable identity plus CAS close fields |

Required invariants:

- role matches org-unit kind;
- at most one open episode per `(org_unit_id, role)`;
- through v12, assignee is active and has a current Part that is a descendant
  of the unit; v13 replaces this with an exact direct Team or Responsibility
  home and forbids a simultaneous current Part membership;
- a write-enabled native higher space has exactly one matching open episode;
- disabling/moving/unassigning the leader either installs an eligible
  replacement or disables writes and closes the episode in the same transaction;
- preview, lease, job, and commit bind episode ID and assignment generation so
  remove/re-add ABA cannot revive stale authority.

### 4.3 `knowledge_distribution_grant`

Higher-native downward exposure is an explicit reference, not a copied item.

Planned identity/evidence:

- source native item and fixed source revision;
- source Team/Responsibility space;
- exact descendant target org unit or target native space;
- grant mode `read_only` only in the first release;
- actor/role episode, operation/effect key, reason, created/revoked timestamps;
- append-only audit and outbox linkage;
- unique active grant for the same source revision/target.

The service proves strict ancestry at commit and read time. Org moves or source
revision changes mark a grant stale/needs review; they never create a mutable
descendant copy.

Grant creation and ordinary revocation bind the current exact Team-leader or
Responsibility-executive episode for the physical source native space. A system
administrator may perform only a break-glass revoke, never create a grant, with
mandatory reason, exact confirmation, CAS, and audit/outbox. Descendant readers,
endpoint editors, stale role episodes, and administrators using ordinary routes
have no distribution mutation capability.

### 4.4 AccessPlan and CorpusClosure

These are typed service DTOs rather than trusted client JSON.

`AccessPlan` contains at minimum:

- actor ID, auth/scope epoch;
- selected view space ID/kind;
- ordered source-space descriptors with actual ID/kind/origin and read
  capability;
- ordered item-scoped distribution overlays with grant ID, source/target space,
  fixed source revision, target scope, and read-only capability; an overlay is
  never represented as whole-source-space access;
- optional native mutation space ID plus a closed capability set drawn only
  from `publish`, `edit`, `deactivate`, `restore`, `curate`, `temporal_mutate`,
  `local_edge_correct`, `aggregate_edge_correct`, `distribution_grant`,
  `distribution_revoke`, `lease_force_release`, and `purge_eligible`;
- optional role episode ID/generation;
- closure generation/hash and organization snapshot version.

Capabilities are resolved against the physical source space for item/local-edge
mutations and `home_space_id` for aggregate-edge correction, never against a
client-selected display view. Inherited items and distribution overlays expose
read only. Personal ownership, active Part membership, or the exact open Team/
Responsibility role episode (whichever applies) is included in the proof and
revalidated at commit. Administrator status alone grants no ordinary Part or
higher authority. Break-glass is a separate capability/proof with the exact
action-by-space limits in `PRODUCT_CONTRACT.md` plus reason/confirmation/audit
requirements; it never appears as ordinary native authoring authority.

The deterministic closure hash covers sorted source space IDs, row versions,
active grant IDs/revisions/generations, organization ancestry, role episode,
and relevant lifecycle statuses. It is revalidated before mutations and
included in retrieval/job cache keys.

## 5. v10 — aggregate graph and semantic automation

### 5.1 Existing `knowledge_edge`

Same-space composite foreign keys remain. The table/repository gains a reviewed
forward representation for automatic origins without weakening endpoint
closure:

- origin adds `semantic_auto` while retaining existing manual/approved origins;
- system-created rows use an explicit actor-kind/job identity shape instead of
  forging a human user;
- endpoint revisions, detector generation, evidence hash, prompt/model/scoring
  version, confidence, and effect key are mandatory for `semantic_auto`;
- automatic rows still satisfy active item, current revision, relation type,
  uniqueness, visibility, and audit/outbox requirements.

v10 uses a forward rebuild of `knowledge_edge`, not an evidence-only companion:
it preserves every existing edge ID and maps existing `manual`/
`semantic_approved` rows byte-for-byte at the DTO level, adds
`origin='semantic_auto'`, and replaces the mandatory human `created_by` shape
with a closed human-actor-or-system-job union. New trigger branches require the
complete automatic evidence/decision row while retaining the historical human-
approval branch unchanged. Pristine and upgraded fingerprints must match; raw
SQL bypass of the rebuilt constraints/triggers is forbidden.

A `semantic_auto` job, decision, or effect cannot insert/update a document-
curation transition, change `knowledge_item.curation_status`, assign a document
finding visibility effect, or mutate search/FTS membership. It consumes only an
active/published current revision; document-curation release remains owned by
the existing matched finding/rescan transaction.

### 5.2 `aggregate_knowledge_edge`

This append-only-identity ledger stores only cross-physical-space semantic
edges.

Required fields:

- edge ID and stable semantic key;
- `home_space_id` and `home_org_unit_id` (the proven LCA);
- source/target space, item, and fixed revision IDs;
- relation type and direction;
- lifecycle `active|inactive|needs_revalidation|suppressed`;
- origin `semantic_auto|manual`;
- actor shape (system job or human), operation/effect key;
- detector/model/prompt/scoring versions and confidence;
- source/target evidence span hashes plus bounded locations, never raw private
  prompt content in audit;
- organization/closure proof hash and endpoint row versions;
- created/updated/deactivated timestamps and row version.

Required constraints/postconditions:

- source and target physical spaces differ;
- endpoint spaces are organizational, never personal;
- `home_space_id` is native Team or Responsibility and is the current LCA;
- same-Team cross-Part and Team-native/descendant edges home at Team;
- cross-Team within one Responsibility and Responsibility-native/descendant
  edges home at Responsibility;
- foreign branches and no-LCA pairs are rejected;
- no self edge, symmetric/directional duplicate, or second active storage at a
  different ancestor;
- fixed revisions belong to their endpoint items/spaces;
- effective visibility additionally requires current readable endpoints and a
  still-valid LCA;
- updates cannot silently change endpoint identity; correction is a state
  transition plus replacement edge when appropriate.

Indexes must bound view reads by home space/state/type and endpoint invalidation
by source/target revision. They must not require scanning all edges when one
revision or org unit changes.

### 5.3 `semantic_edge_intent`

This insert-only ledger closes the deployment gap between published content and
the later automatic-edge handler. It is not a `durable_job` and is never claimed
or acknowledged by the generic projection outbox worker.

No new mutable item counter is needed. `item_lifecycle_generation` is derived
exactly from the existing immutable deactivation episodes: `1` when there has
never been an episode, `2*episode_no` while the latest episode is open/inactive,
and `2*episode_no+1` after that episode is restored/active. Purged/merged items
are never eligible. The intent's closed source-lifecycle vector is
`(item_lifecycle_generation,
personal_wiki_profile.lifecycle_generation_or_0)`; organizational spaces always
use zero for the second component. The service stores the value plus latest-
episode proof in the intent, and v10 triggers/postconditions validate the formula.
Rename/default/edit/curation/role changes do not alter either lifecycle
component.

Required fields/invariants:

- immutable intent ID and globally unique stable effect key;
- source physical space/item/current published revision and its body/evidence
  hash;
- target kind `local|aggregate`, exact intended target/home space and
  organization unit where applicable;
- detector generation plus captured revision, closure, organization, and target
  generation fences;
- the exact source-lifecycle vector captured after the activating transition;
- provenance kind
  `publication|baseline_backfill|org_retarget|detector_reseed|lifecycle_reactivate`
  with a strict
  tagged proof: publication transition/audit/same-transaction ordinary-outbox
  IDs only for `publication`, otherwise exact seed-run/snapshot-high-water/cursor
  plus organization-event, detector-rollout, or item/Wiki lifecycle-restore ID
  where applicable;
- created timestamp and strict insert-only UPDATE/DELETE rejection;
- one row/effect key per `(source_revision_id, target_kind, target_space_id,
  detector_generation, organization_generation, source_lifecycle_vector)`, so
  publication/backfill races at one generation converge while organization or
  reversible-lifecycle change intentionally re-evaluates even when revision and
  target IDs are unchanged; indexes support bounded monotonic batches and an
  indexed anti-join to `durable_job.effect_key`.

The publication producer inserts intents only when the current revision
transitions to active/published; provisional/quarantined revisions insert none.
The separate `semantic_edge_intent_seed_run` ledger has cause kind
`baseline_backfill|org_retarget|detector_reseed|lifecycle_reactivate`, canonical
domain/audit high-water, optional old/new ancestor or detector generations,
stable keyset
cursor, `pending|running|completed|failed` state/fence, counts/checksum, and
audit/outbox identity. Legal transitions are exactly `pending -> running`,
`running -> running|completed|failed`, and `failed -> running` with a strictly
higher fence; `completed` is terminal, the cursor never decreases, and retry
resumes strictly after the committed cursor. Each transaction scans at most 200
source revisions and inserts only the frozen constant number of eligible scope
intents per revision; there is no Wiki/space/model fan-out. Baseline covers every
pre-existing current active/published revision, detector reseed covers that same
current corpus under the new generation, and organization retarget covers only
the affected subtree/new ancestor chain. Lifecycle reactivation binds the exact
canonical item/Wiki restore event and seeds only that item or that Wiki's current
active/published revisions under the new lifecycle vector. The consumer follows
its own monotonic restore-event high-water regardless of generic outbox state.
Publication/seed races converge through
the deterministic intent/effect key. A non-publication seed proof cannot populate
publication proof fields.

Before automatic-edge flags are effective, intents may accumulate but semantic
`durable_job` rows remain zero.
`SBE-4.2` first registers the exact handler pair, then a bounded materializer
captures current revision/visibility, closure, authorization, target/LCA,
generation and suppression observations as immutable job input/fences and, in
one short transaction, inserts exactly one initially `queued` known-handler job
for every flag-eligible intent absent from the indexed effect-key join. It never
drops an unjoined intent merely because that observation is stale. Concurrent/
restarted materializers converge through the existing unique job effect key, and
a completed drain has zero flag-eligible unjoined intents. Claim and edge commit
repeat all applicable revalidation; invalid/stale input closes its one job with a
deterministic pre-candidate `job_terminal` no-edge decision rather than being
selected again. An ordinary outbox state change does not mark an intent consumed,
and no event acknowledgement deletes the intent.

### 5.4 `semantic_edge_decision` and suppression

Every terminal automatic job has exactly one immutable `job_terminal` decision
record; every queued/claimed/running/retry automatic job has zero terminal and
candidate decision rows. Zero or more immutable `candidate` records, the one
terminal record, any edge effect, and the durable-job terminal transition commit
in the same handler-finalization transaction. Retry exhaustion/dead and every
other automatic-job terminal path use that same postcondition. The closed tagged
union makes stale, no-candidate, provider-failure, rejected, and activated
outcomes durable without inventing a candidate target.

Common evidence is source revision, intended target/home/LCA, job/intent effect
key, detector generation, and deterministic terminal/result reason. A
`candidate` record additionally requires candidate target revision,
candidate-retrieval version/score components, model/provider/prompt schema
(which binds the supplied relation/direction policy), response digest, parsed
relation/direction/grade, canonically located evidence
spans derived from verbatim body quotes, deterministic validation result, and
resulting local/aggregate edge ID only when activated. The provider never owns
or calculates persisted Unicode offsets.
A pre-candidate `job_terminal` outcome requires candidate/model/edge fields to be
null; a completed job with candidates binds their ordered decision IDs and
aggregate digest. Strict CHECKs reject every incoherent tag/null/result tuple.

`semantic_edge_suppression` records edge semantic key, scope, reason, actor,
detector-generation floor/ceiling policy, timestamps, and optional superseding
edge. Automatic jobs consult it before activation. Suppression/deactivation is
audited and reversible only through an explicit correction operation.

Correction authorization resolves against a local edge's physical space or an
aggregate edge's `home_space_id`. Personal owner and active Part editor govern
their local edges; the exact Team leader governs Team-native and Team-home
edges; the exact executive governs Responsibility-native and Responsibility-
home edges. A system administrator has audited break-glass suppress/deactivate
authority only for organizational edges. Lifting suppression requires the
current native/home human authority, writes a new correction event, and queues
validator re-evaluation; it never flips stale evidence directly active.

### 5.5 Aggregate generation vector

Higher Index/Log/Graph state depends on multiple sources. Store a deterministic
summary keyed by selected higher space:

- sorted `(source_space_id, content_generation, graph_generation,
  temporal_generation)` vector hash;
- home aggregate-edge generation;
- built/desired generation and immutable manifest hash;
- closure/org version.

A child mutation invalidates only the union of affected ancestor chains.
Ordinary content edits use the current chain; an organization move captures and
invalidates both pre-move and post-move chains in the same transaction/outbox
contract. Authorization epoch changes hide former-ancestor data immediately
even if its replaceable projection has not rebuilt. Projectors query/filter only
those targets; they do not scan every space.

### 5.6 `knowledge_explicit_reference`

Explicit author references use a separate deterministic ledger and do not
inflate semantic graph state.

Required fields/invariants:

- reference ID, source space/item/fixed revision, ordinal, and exact syntax
  version;
- target space/item and target revision observed at creation;
- closure/authorization proof, actor, operation/effect key, timestamps;
- lifecycle derived from source revision/current target visibility, with
  unavailable reason but no silent retarget;
- unique `(source_revision_id, ordinal)` and exact replay identity;
- target must have been readable in the publication `CorpusClosure`; concealed
  targets fail without ID disclosure;
- a higher native source may target its descendant closure, while a Part source
  cannot target a sibling/foreign space;
- no row in local or aggregate semantic-edge tables is created as a side effect.

Projection/API exposes a separate ordered References list. Retrieval treats an
authorized explicit reference as bounded structured context rather than a
semantic graph hop.

## 6. v11 — explicit higher publication and curation authority

v11 preserves every v1-v10 migration byte and every legacy
`personal|part|both` row. It adds an explicit, immutable authority lane for a
single Team or Responsibility native publication target and extends reviewed
curation to the exact current higher role episode. No administrator or
descendant-reader shortcut is introduced.

### 6.1 `higher_publication_preview_authority`

Each explicit higher preview has exactly one immutable authority row. The row
binds the preview ID, actor and scope epoch/row version, selected native
`space_id`/kind/org unit, exact role assignment ID/generation/row version,
`CorpusClosure` hash/generation, organization snapshot version, canonical target
descriptor JSON/hash, and creation time. The target descriptor contains exactly
`space_id`, `kind`, `capability='publish'`, `role_assignment_id`, and
`role_assignment_generation`.

The authority row and preview use deferred same-transaction referential binding
so the authority can be validated before the preview insert without permitting a
committed orphan. An explicit preview stores only `['explicit']` in the legacy
mode array; the descriptor is returned separately. Legacy previews have no
higher authority row and retain their original array and response behavior.

Insert and active transition checks require the actor, native space, org unit,
write flag, role kind, role episode, scope epoch, actor row version, and
descriptor values to be exact. Extraction jobs bind the same selected native
space, `publish` capability, closure proof, and role episode. Cancellation or
expiry remains possible after role loss, but extraction/ready/commit effects do
not.

### 6.2 explicit publication operation authority

`publication_operation` is rebuilt forward with `target_mode='explicit'` and a
nullable unique explicit-authority reference. Historical rows copy unchanged
with a null reference; `both` remains the only mode with a lineage group.

`higher_publication_operation_authority` immutably binds one operation to one
preview authority and repeats the exact commit-time actor, target, role,
closure, organization, and descriptor proof. Deferred foreign keys require the
matching operation and exactly one matching `publication_target` to exist at
transaction commit. The operation insert, target insert, replay path, and
service commit all verify the same target and current open role episode.

Role replacement, disable, organization move, scope-epoch change, actor-row
change, space write disable, or closure drift after preview therefore rejects
the commit with zero partial publication effects. Reassignment of the same user
does not revive an old preview because assignment ID and generation are bound.

### 6.3 higher reviewed curation

The existing `space_editor` finding transition accepts Team and Responsibility
only when the reviewer is active and holds the exact open role episode whose
unit and role match the finding's physical native space. Personal and Part
branches remain unchanged. Administrator status alone, descendant membership,
Part leadership, a foreign role, and a closed/stale episode grant no higher
review authority. The service still resolves and revalidates
`Capability.CURATE` inside the mutation transaction before the trigger checks
the physical row.

## 7. v12 — temporal provenance and presentation footer

### 7.1 `revision_temporal_assertion`

Append-only event streams bind time meaning to an exact revision without
changing the canonical body. A small CAS-protected head table/pointer identifies
the stream head; event rows are never updated/deleted.

Required fields:

- assertion ID, item/revision/space IDs;
- kind `source_modified|information_effective`;
- tagged value representation: `instant` stores UTC epoch milliseconds and
  `date` stores an exact proleptic-Gregorian `YYYY-MM-DD`; kind/value/precision
  combinations are closed and independently validated;
- source timezone/offset and precision;
- origin `user|trusted_upstream|reviewed_legacy_manifest|system_import`;
- evidence digest/reference, asserted actor/time;
- predecessor/head generation and event kind `assert|revoke`;
- DB-assigned monotonic stream sequence, expected-head CAS, operation/effect
  key, and deterministic summary hash. A user-supplied effective/source time is
  never used to order events.

No service infers `information_effective` from file mtime, upload time, Notion
`updated_at`, or migration time. Future effective dates are legal and presented
as their exact date. A static projection does not label them scheduled/current
from the wall clock. Revoking the head yields no active value; an older
superseded value does not silently become active. A new content revision starts
with no assertion heads unless an adapter explicitly creates new evidence-bound
assertions for that revision.

`system_import` is an actor/origin label, not evidence by itself. It may write a
source-modified assertion only from an exact reviewed legacy/upstream fact; the
import transaction time cannot become source-modified or information-effective.

### 7.2 `revision_verification_attestation`

Append-only verification binds to one exact revision:

- verifier actor kind/user ID and authority proof;
- DB-assigned `attested_at` (the UI/API `last_verified_at`), method, evidence
  digest, bounded note/reason, plus optional externally claimed
  `subject_verified_at` provenance that never orders/selects the head;
- exact `temporal_summary_sha256` plus predecessor/head generation;
- append-only `attest|revoke` event, expected-head CAS, and operation key.

A new content revision has no current verification until separately attested.
Old attestations remain history but are never inherited. A temporal head change
changes the summary hash and makes the old attestation non-current; revocation
does not fall back to a superseded attestation.

Temporal head services revalidate native write authority immediately before
commit. Personal owner, active Part editor, exact Team leader, and exact 담당
executive are scoped to their native revisions. Ancestor inherited-read access
does not grant assertion/attestation authority. Automated checks use a separate
diagnostic record/status and cannot create this attestation type.

### 7.3 Projection manifest v2

For presentation Markdown, the manifest records at minimum:

- item/revision IDs and the current positive integer `revision_no`;
- `body_encoding='utf-8'` and
  `body_normalizer_version='markdown_lf_v1'`;
- `projected_body_size_bytes`, the exact byte length of the LF-normalized UTF-8
  prefix written before the footer;
- `projected_body_sha256`, the exact hash of that prefix, plus separately named
  canonical/source hashes already owned by the revision/artifact contracts;
- `footer_schema_version`;
- rendered size and SHA-256;
- deterministic temporal summary version/hash and content/graph/temporal
  generation tuple;
- `snapshot_as_of_at` plus canonical audit/domain high-water ID from the pinned
  snapshot; projector `built_at` is not a manifest field.

Transition rule: retain v1 canonical-body `sources/*.md` and introduce v2
presentation `pages/*.md` until every consumer understands the manifest-bound
body boundary. A later packet may switch names only with a migration/export
compatibility gate.

Re-import removes a footer only when rendered hash/size, canonical prefix
length/hash, and deterministic suffix all match a trusted manifest/export
envelope. This proves the projected LF-normalized logical body; it does not
claim to reconstruct a pre-normalization CRLF/raw source byte stream. Trust
requires either the exact manifest digest anchored in the canonical projection
ledger plus a bound projection root identity, or a portable envelope signature
under an approved deployment signing-key ID. A user-supplied adjacent manifest
or self-declared hash is insufficient. Delimiter or regular-expression guessing
is forbidden.

Footer v2 bytes use the exact fixed grammar in `PRODUCT_CONTRACT.md`: UTF-8,
`markdown_lf_v1` body prefix, two literal LF separator bytes, three required
rows (`생성`, `마지막 내용 수정`, `Revision`) followed by zero to three
evidence-backed rows in fixed source/effective/verification order, fixed
Markdown line endings/trailing LF, and typed generated values only. Absent,
revoked, superseded, or non-current optional metadata omits the whole row;
placeholder values are forbidden. Golden inline/blob/empty/CRLF/Unicode fixtures
cover all eight legal optional-row presence combinations.

### 7.4 Temporal generation and boundary time

Each space has a monotonic `temporal_generation` independent of content/FTS and
graph generations. A successful temporal assertion/verification event:

1. appends the event and CAS-updates its head;
2. increments only the affected space temporal generation;
3. appends the same-transaction outbox/projection request;
4. invalidates current ancestor aggregate generation vectors;
5. leaves content revision, FTS, embedding, and semantic evidence unchanged.

The same transaction appends one audit/outbox event. Projection Log includes
assert/supersede/revoke/attest/verification-revoke exactly once, ordered by
canonical DB commit time and audit ID rather than claimed fact time.

Projection identity is derived from the content/graph/temporal generation tuple
and immutable manifest; old generations are not overwritten. Reranker/cache
keys include the temporal summary and request `as_of`. `temporal-cache-v1` uses
the exact supplied UTC epoch millisecond when `as_of` is explicit. For implicit
now it captures request-start UTC once and keys by its 60,000-ms floor. Expiry
is the earliest of that bucket's exclusive end or the next authorized known
effective boundary; a date-only boundary is 00:00:00 in IANA `Asia/Seoul` on
that date. Entries at/after the boundary recompute, and authorization/content/
graph/temporal generation changes invalidate independently.

`temporal_summary_sha256` is independently reproducible as SHA-256 over
canonical JSON with exact keys `schema_version`, `revision_id`,
`source_modified_head`, and `information_effective_head`. A head includes event
ID, state, and tagged value (or explicit revoked/null state). It excludes every
verification/attestation, audit, footer, projection, and wall-clock field to
avoid self-reference. Golden hashes cover empty, asserted, superseded, and
revoked heads.

`snapshot_as_of_at` is derived from the pinned canonical domain/audit high-water
for the generation and is included with its high-water ID in the immutable
manifest. Worker completion `built_at` lives only in operational
`projection_state` telemetry outside manifest/rendered bytes and their digests.

## 8. v13 — direct organization-home affiliation

`org_home_affiliation` is the append-only common employment-home ledger. Every
active organization-affiliated user has at most one open row whose
`home_kind` exactly matches its active org unit:

- ordinary users and Part leaders have a `part` home mirrored one-for-one from
  the existing open `org_membership`; `app_user.current_part_id` remains the
  compatibility pointer for this case only;
- a Team leader has a direct `team` home, has no open Part membership, and can
  write only the matching Team-native space through the independently fenced
  open `higher_scope_role_assignment`;
- a Responsibility executive has a direct `responsibility` home, has no open
  Part membership, and can write only the matching Responsibility-native space
  through the independently fenced open role assignment;
- a user cannot hold two current homes. Moving between levels closes the old
  home and role episode before opening the new home and role episode; stale
  previews, jobs, sessions, and leases remain fenced by scope/role generation.

The v13 upgrade mirrors all existing Part memberships without changing their
IDs. It refuses any database containing an open legacy higher-role episode,
because a descendant-Part role cannot be silently reinterpreted as an
employment home. An administrator must explicitly close/resolve that legacy
state before retrying the forward-only migration.

The registered v13 physical schema fingerprint is
`2326851c0be29ba6ee5f44ae9886cc9cf4c24d2b9a4f369e52574515fd69a2b9`;
the migration checksum is
`e5a35fecf7005c240b0b48002c354c10446c28025b85af6b1eef37ba4092d711`.

## 9. Forward API changes

The frozen v7 baseline is 52 paths/56 operations. The `SBE-0.1` registration,
including the approved SBE-6.2 authority-read amendment, adds the following
18 paths/22 operations and established the historical 70 paths/78 operations
surface. The approved SBE-8.12 original-source amendment adds exactly two v1
GET paths/operations below. The current registered total is therefore
72 paths/80 operations, including 27 GET operations: baseline 52/56 + the
existing successor 18/22 + SBE-8.12 2/2. Baseline operation IDs and hashes stay
frozen; none of these operations may be renamed, split, or combined without an
explicit user-reviewed contract change.

| Method and exact path | Exact `operationId` | Contract |
|---|---|---|
| `GET /api/knowledge/v2/personal-wikis` | `personalWikisList` | owner/lifecycle/query-bound cursor list; active or archived-management mode |
| `POST /api/knowledge/v2/personal-wikis` | `personalWikisCreate` | owner create with exact default postcondition |
| `POST /api/knowledge/v2/personal-wikis/search` | `personalWikisSearch` | active-only read-only all-Wiki management search, final max ten |
| `PATCH /api/knowledge/v2/personal-wikis/{space_id}` | `personalWikiRename` | owner rename with display/key CAS |
| `PUT /api/knowledge/v2/personal-wikis/{space_id}/default` | `personalWikiSetDefault` | owner exact-one-default transaction |
| `POST /api/knowledge/v2/personal-wikis/{space_id}/archive` | `personalWikiArchive` | owner-only reversible archive |
| `POST /api/knowledge/v2/personal-wikis/{space_id}/restore` | `personalWikiRestore` | owner-only same-ID restore |
| `GET /api/admin/v2/org/teams/{team_id}/leader` | `teamKnowledgeLeaderGet` | admin-only exact current Team role episode and native write-state read |
| `PUT /api/admin/v2/org/teams/{team_id}/leader` | `teamKnowledgeLeaderPut` | assign/replace/end-and-disable exact Team-leader episode |
| `GET /api/admin/v2/org/responsibilities/{responsibility_id}/executive` | `responsibilityKnowledgeExecutiveGet` | admin-only exact current responsibility role episode and native write-state read |
| `PUT /api/admin/v2/org/responsibilities/{responsibility_id}/executive` | `responsibilityKnowledgeExecutivePut` | assign/replace/end-and-disable exact executive episode |
| `GET /api/knowledge/v2/spaces/{space_id}/distribution-targets` | `distributionTargetsList` | current source-native role only; server-derived descendant target closure |
| `GET /api/knowledge/v2/spaces/{space_id}/distributions` | `distributionsList` | authorized bounded current/history list |
| `POST /api/knowledge/v2/spaces/{space_id}/distributions` | `distributionsCreate` | current source-role grant only |
| `POST /api/knowledge/v2/spaces/{space_id}/distributions/{grant_id}/revoke` | `distributionRevoke` | current source role or admin break-glass revoke |
| `POST /api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppress` | `semanticEdgeSuppress` | exact local/home authority or organizational admin break-glass |
| `POST /api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppression/lift` | `semanticEdgeSuppressionLift` | current local/home human authority; enqueue revalidation only |
| `GET /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal` | `itemTemporalGet` | current typed heads, attestation, and bounded history cursor |
| `POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal-assertions` | `temporalAssertionCreate` | assert source/effective head with expected-head CAS |
| `POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal-assertions/{assertion_id}/revoke` | `temporalAssertionRevoke` | exact-head revoke/no fallback |
| `POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/verification-attestations` | `verificationAttestationCreate` | revision+temporal-summary-bound attest |
| `POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/verification-attestations/{attestation_id}/revoke` | `verificationAttestationRevoke` | exact-head verification revoke/no fallback |

The SBE-8.12 amendment registers these two additional operations without
changing any existing route or migration:

| Method and exact path | Exact `operationId` | Contract |
|---|---|---|
| `GET /api/knowledge/v1/spaces/{space_id}/items/{item_id}/source-artifacts` | `listItemSourceArtifacts` | complete retained original-source metadata for one item after current selected-view authorization; no pagination or query parameters |
| `GET /api/knowledge/v1/spaces/{space_id}/items/{item_id}/source-artifacts/{artifact_id}/download` | `downloadItemSourceArtifact` | original bytes verified outside the transaction, followed by authority/metadata revalidation and a private no-store attachment |

Existing generic v1 space/item/conversation/publication/graph/job routes remain
and gain only versioned additive successor shapes behind their flags. The
complete registered successor delta is the 22 v2 operation IDs above plus the
two SBE-8.12 v1 operation IDs, across 20 additional paths. SBE-8.12 remains
`IMPLEMENTED_NOT_GATED`; contract registration alone neither proves its
external gates nor changes runtime exposure.

The successor adds exactly these stable public domain codes:
`WIKI_NAME_CONFLICT`, `WIKI_DEFAULT_REQUIRED`,
`WIKI_ARCHIVED_HISTORY_ONLY`, `ROLE_EPISODE_STALE`,
`NATIVE_WRITE_DISABLED`, `CORPUS_CLOSURE_STALE`,
`DISTRIBUTION_STATE_CONFLICT`, `EDGE_HOME_INVALID`, `EDGE_SUPPRESSED`,
`REFERENCE_TARGET_INVALID`, `TEMPORAL_HEAD_CONFLICT`,
`TEMPORAL_VALUE_INVALID`, `VERIFICATION_NONCURRENT`, and
`PROJECTION_MANIFEST_UNTRUSTED`. Authentication, foreign/concealed IDs, generic
validation, CAS, lease, idempotency, and rate/size failures reuse the existing
stable codes; responses never reveal whether a concealed target exists.

`SBE-0.1` registers OpenAPI/JSON Schema shapes for:

- owner-only cursor-paginated Wiki list/create/rename/default/archive/restore,
  including a separate archived-only management list/search;
- `POST /api/knowledge/v2/personal-wikis/search`, a read-only, active-only,
  owner-bound management search returning at most ten origin-preserving results
  and never mutating or overriding a conversation;
- selected-view descriptors and per-source `CorpusClosure` capabilities;
- publication targets with explicit `space_id`, kind, role episode, and
  capability instead of only `personal|part|both` strings;
- history-only archived Wiki/session errors and restore links;
- origin-preserving aggregate search/graph responses;
- automatic-edge evidence/status/suppression/correction;
- explicit-reference parsing, ordered list, unavailable state, and exact target
  metadata;
- authorized full-corpus retrieval evidence plus a deterministic, document-
  count-unbounded `ranked_documents[]` core, an exact 96 KiB Agent excerpt
  budget, and separately variable bounded fallback status/reason envelope;
- typed `registered_at`, `content_modified_at`, `source_modified_at`,
  `information_effective_at`, and `last_verified_at` metadata;
- projection manifest v2 body/footer hashes.

Existing clients receive versioned compatibility responses until the new UI and
contract tests pass. A compatibility adapter may map legacy `personal` to the
current active default Wiki; it must never guess between multiple Wikis once an
explicit `space_id` is available.

## 10. Schema acceptance gate

Every version must prove:

1. pristine create equals sequential v1->current upgrade;
2. exact old IDs/bodies/history preserved;
3. interrupted/failing migration leaves exact prior fingerprint;
4. repeated migrator run is a verified no-op;
5. all new uniqueness, role, lifecycle, LCA, actor, and temporal postconditions;
6. foreign key and integrity checks clean;
7. unknown/tampered schema and unsafe nonempty legacy states fail closed;
8. one/100/1,000-Wiki indexes retain bounded query plans;
9. old v7 behavior remains available behind disabled successor flags;
10. no live DB, source root, or production flag was changed by tests.
