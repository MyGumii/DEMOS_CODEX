# Second Brain evolution product contract

Status: user-approved, machine-registered target behavior. Runtime activation is
still gated packet-by-packet by `EXECUTION_PLAN.md`.

## 1. Terminology

- **Wiki:** one personal `knowledge_space` owned by one user. A Wiki is an
  independent publication, retrieval, graph, projection, and conversation
  boundary.
- **Native organizational space:** the canonical Part, Team, or Responsibility
  space owned by that organization unit.
- **Selected view:** the single scope selected for a conversation or browser
  view.
- **CorpusClosure:** the exact, server-authorized set of physical source spaces
  plus item-scoped distribution overlays readable through a selected view. It
  is computed from current organization/grant state; the client cannot enlarge
  it. An overlay never grants the rest of its source space.
- **Inherited item:** an item physically owned by a descendant source space but
  displayed in an ancestor selected view. Its real `source_space_id` is never
  rewritten.
- **Aggregate edge:** a semantic edge whose endpoints are in different physical
  organization spaces. It is stored once at the endpoints' lowest common
  organizational ancestor (LCA).
- **Canonical body:** user-authored Markdown stored in
  `knowledge_revision_body`. Presentation footers and projection metadata are
  not part of it.

## 2. Cross-cutting invariants

1. The canonical SQLite database owns identity, permissions, Wiki lifecycle,
   content, revisions, graph state, audit, jobs, and projection generations.
2. Files remain replaceable projections. Deleting a projection cannot delete
   canonical knowledge.
3. Every request is authorized from server-side identity and current rows.
   Client-supplied Wiki, organization, role, edge, or cache data is only a
   request hint.
4. Every mutation is transactionally coupled with audit, outbox, idempotency,
   and compare-and-swap state required by that mutation.
5. LLM/OCR/network/file hashing runs outside DB transactions. Inputs and
   authority are revalidated immediately before commit.
6. A provider timeout, malformed response, missing model, or Codex/company API
   outage may reduce semantic enrichment; it must not corrupt state or break the
   deterministic retrieval fallback.
7. Personal, Part, Team, and Responsibility corpora never acquire data merely
   because another level is visible in the UI. Publication, inheritance, and
   distribution have distinct, auditable meanings.

## 3. Personal Wiki collection and lifecycle

### 3.1 Collection

- A user may create any number of personal Wikis. There is no product-level
  numeric cap such as 10.
- Limits still exist for storage bytes, item count, upload size, concurrent
  jobs, request rate, and bounded query work. These are operational resource
  budgets, not a Wiki-count limit.
- The personal space that already exists at migration time keeps its ID and
  becomes the user's active default Wiki.
- Every user has exactly one active default Wiki. Creating another Wiki does
  not silently change the default.
- Create always assigns the authenticated caller as owner. Only that owner can
  rename or set the default through ordinary product APIs; administrator status
  alone supplies no personal-Wiki lifecycle capability.
- Names are editable metadata. Renaming a Wiki does not change its ID, item IDs,
  graph IDs, conversation bindings, or projection history.
- Names are NFKC+casefold normalized only into a collision key within one
  owner. The bounded, trimmed display spelling is stored separately exactly as
  submitted; normalization never rewrites its visible Korean/Latin spelling.

### 3.2 Archive and restore

- Archive is an explicit, reversible space lifecycle action. It is not item
  deactivation, conversation archive, deletion, purge, or automatic aging.
- An archived Wiki preserves the same space, items, revisions, graph, audit,
  projection history, and conversation bindings.
- Archived Wikis are excluded from the normal active tree, new uploads, edits,
  automatic semantic jobs, conversation creation, and global active-Wiki
  search.
- A user can reveal archived Wikis in a management view, inspect them read-only,
  search that archived-only list with a bounded cursor, and restore them. This
  management search never feeds Agent retrieval or reports archived hit counts
  through the active-Wiki search surface.
- Opening an old conversation bound to an archived Wiki selects that Wiki in a
  history-only read-only state. Continuing the conversation requires restoring
  the Wiki or starting a new conversation in an active Wiki.
- The current default cannot be archived. The last active Wiki cannot be
  archived. The user must first create/restore another active Wiki and make it
  default.
- Only the Wiki owner can archive or restore it through the ordinary product
  API. An administrator has no ordinary personal-Wiki archive/restore override;
  any future legal-hold or account-recovery tool is a separate reviewed contract.
- Wiki archive and restore each advance a dedicated lifecycle generation without
  rewriting item/revision IDs. Restore emits one canonical event; the automatic-
  edge service later turns it into a bounded affected-Wiki reactivation seed, so
  jobs that terminally rejected the archived generation do not suppress the
  restored generation.
- No background task auto-archives Wikis.

### 3.3 Listing and navigation

- Wiki listing is cursor-paginated, searchable, and lazily/virtually rendered.
  The server never returns every Wiki or every child item without a bound.
- The left Knowledge tree orders top-level scopes as `내 지식 -> Part -> 팀 ->
  담당`.
- `내 지식` expands into the user's personal Wikis. Each Wiki expands into its
  documents, Index, Log, Graph, and management actions.
- A renamed or archived/restored Wiki updates navigation through canonical
  events; browser local state is only presentation state.

## 4. Conversation-to-Wiki binding

- Every conversation stores exactly one authoritative selected `space_id`.
- A personal conversation searches only its bound Wiki. It does not silently
  search the user's other Wikis.
- Opening a previous session loads its server-stored `space_id` and
  automatically selects the corresponding Wiki. A browser cache may make this
  faster but cannot override the server binding.
- Switching the selected Wiki or organization view starts a new conversation.
  Existing messages, summaries, pins, retrieval cache, and citations remain
  bound to the old selection.
- Conversation cache keys include the bound space/view ID, authorization epoch,
  corpus-closure generation, item revisions, graph generation, temporal summary
  version, and ranker/model versions.

## 5. Publication and editing

### 5.1 Upload dialog

- The Knowledge `+` control opens one dialog with Chrome-like tabs:
  `자료 업로드` first and `대화 지식화` second.
- Before processing content, the user chooses `개인`, `Part`, or `개인+Part`.
  Choosing `개인` or `개인+Part` also requires an explicit active personal
  Wiki; the current default is preselected but remains visible.
- `개인+Part` creates two independent canonical outcomes in one transaction.
  Later personal edits never modify the Part item. A later UI may offer an
  explicit reviewed republish/update, but never silent synchronization.

### 5.2 Part, Team, and Responsibility writes

- Every active Part member retains the documented Part collaboration rights.
  Ordinary users and Part leaders are directly affiliated with that Part.
- A Team leader is directly affiliated with the Team, is not synthesized as a
  member of any descendant Part, and can publish/edit only that Team's native
  documents while the matching Team-leader episode is open.
- A Responsibility executive is directly affiliated with the Responsibility,
  is not synthesized as a Team or Part member, and can publish/edit only that
  Responsibility's native documents while the matching executive episode is
  open.
- Every user has at most one current organization home (`part`, `team`, or
  `responsibility`). Role episodes remain separate fencing records; changing a
  person's home or role cannot reuse an earlier episode's authority.
- Role removal, replacement, user disable, or organization move invalidates old
  previews, leases, and jobs. Replacement or write-disable occurs in the same
  transaction so a write-enabled unit is never leaderless.
- System administrators manage roles and may perform documented emergency
  lock/purge operations. Administrator status is not a blanket right to author
  ordinary Team/Responsibility knowledge.
- Inherited descendant items are always read-only in an ancestor view. A higher
  leader edits only items physically owned by the native higher space.

### 5.3 Higher-native downward visibility

- A Team/Responsibility native document is visible in that selected level by
  default.
- It is not automatically copied into descendant spaces and does not silently
  change Part answers.
- The exact current source-native Team leader or Responsibility executive may
  create an explicit, audited distribution grant to a descendant scope.
  Descendants then see a read-only reference to the original item; they do not
  receive a mutable copy.

### 5.4 Exact mutation authority

Every service resolves one named capability from current canonical state. A
client-supplied role, scope, or capability is never authority.

| Mutation | Personal Wiki | Part native | Team native / Team-home | Responsibility native / Responsibility-home |
|---|---|---|---|---|
| Publish, edit, deactivate, restore, reviewed curation, temporal assert/attest/revoke | exact owner | any active member while Part writes are enabled | exact open Team-leader episode, native items only | exact open executive episode, native items only |
| Correct a same-space semantic edge | exact owner | any active member while Part writes are enabled | exact Team leader for Team-native endpoints | exact executive for Responsibility-native endpoints |
| Correct an aggregate semantic edge | not applicable | not granted merely by editing an endpoint | exact Team leader for an edge whose home is that Team | exact executive for an edge whose home is that Responsibility |
| Grant or revoke downward distribution | not applicable | not applicable | exact Team leader, Team-native source only | exact executive, Responsibility-native source only |
| Release own lease with the holder tuple | holder owner | holder member | holder Team-leader episode | holder executive episode |
| Force-release another or stale lease | owner in that Wiki | exact Part leader | exact Team leader | exact executive |

Additional authority rules:

- An endpoint editor, descendant member, Part leader, or foreign higher leader
  cannot mutate a Team/Responsibility-home aggregate edge merely because they
  can read one endpoint.
- A correction may suppress/deactivate an edge with a mandatory reason. Lifting
  suppression is a separate operation by the same current home authority; it
  queues current-revision re-evaluation and never directly reactivates stale
  evidence.
- A distribution grant and revoke require the current source-space role
  episode. Role loss fences both even if the same user is later reassigned.
- System-administrator status alone grants no ordinary content authority in any
  space. An administrator who independently owns a Wiki, has active Part
  membership, or holds a current higher role receives only that independent
  authority. Break-glass scope is closed as follows:

  | Administrator break-glass mutation | Personal | Part | Team | Responsibility |
  |---|---:|---:|---:|---:|
  | Force-release another/stale lease | Yes | Yes | Yes | Yes |
  | Suppress/deactivate a semantic edge | No | Yes, local | Yes, native/home | Yes, native/home |
  | Revoke an existing distribution grant | n/a | n/a | Yes | Yes |
  | Execute an already-eligible permanent purge | Yes | Yes | Yes | Yes |
  | Publish/edit/deactivate/restore/curate/temporal mutate, lift suppression, activate edge, create distribution, Wiki archive/restore | No | No | No | No |

  Each allowed break-glass command requires a bounded reason, exact
  confirmation, CAS, audit/outbox, and current scope revalidation.
- Every capability above also requires active account/unit/space, current
  ownership or membership as applicable, write rollout, current role episode,
  closure/authorization epoch, lease/fence where applicable, expected revision/
  row version, and idempotency revalidation immediately before commit.

## 6. Authorized aggregate views

The resolver returns an `AccessPlan` containing the selected view, native write
target (if any), role episode, and exact `CorpusClosure`.

| Selected view | Readable physical source spaces | Native write target |
|---|---|---|
| Personal Wiki | That exact active Wiki; archived only in history mode | That Wiki |
| Part | That exact Part plus explicitly granted higher-native item overlays | That Part |
| Team | Team native space, every active descendant Part, plus explicitly granted 담당-native item overlays | Team native space for exact Team leader only |
| Responsibility | Responsibility native space, every descendant Team native space, and every descendant Part | Responsibility native space for exact executive only |

Additional rules:

- Personal Wikis are never included in organizational closure.
- Item-scoped distribution overlays carry exact grant/source revision IDs and
  do not make the origin higher space generally searchable/readable.
- Ordinary members may read their ancestor Team and Responsibility closures.
  Foreign branches remain concealed, not merely forbidden after disclosure.
- Organization move/disable changes the closure immediately and invalidates
  stale sessions/jobs by epoch/generation fencing. A move invalidates the union
  of the old and new ancestor chains so the former Team/담당 cannot retain stale
  cache/projection visibility.
- Responses expose both `selected_view_space_id` and real `source_space_id`,
  plus `inherited` and per-source capability metadata. They never rewrite a
  Part item as if Team physically owned it.

## 7. Graph topology and automatic semantic edges

### 7.1 Storage and visibility

- Existing `knowledge_edge` remains a same-physical-space edge ledger.
- Same-Part edges remain inside that Part and never appear in another Part's
  own graph.
- Cross-Part endpoints under the same Team have Team as their LCA/home.
- Cross-Team endpoints under the same Responsibility have Responsibility as
  their LCA/home.
- An endpoint in a native higher space and an endpoint in its strict descendant
  use the higher space as LCA/home.
- No personal-to-organization aggregate semantic edge is created
  automatically.
- An aggregate edge is stored exactly once. Team/Responsibility views compose
  descendant local clusters and applicable LCA edges at read/projection time;
  they do not duplicate child bodies or edges.
- A Part graph never exposes Team/Responsibility aggregate edges. A Team graph
  exposes its Parts' local clusters and Team-home edges. A Responsibility graph
  additionally exposes descendant Team-home and Responsibility-home edges.

### 7.2 Automatic edge pipeline

"Automatic" means no human approval click after a candidate passes all gates;
it does not mean blindly connect every document.

This section governs Graph-edge activation only. It does not approve document
updates, replacements, merges, quarantine, or conflict winners and never
bypasses ADR-009 item-curation visibility.

1. Publication commits the canonical item/revision. A revision whose versioned
   document-curation gate resolves to `published` enters FTS and Agent retrieval
   in that same transition; provisional or quarantined content never does.
2. The transaction that makes the current revision active/published also appends
   one immutable, handler-independent `semantic_edge_intent` per eligible local/
   ancestor scope and the ordinary publication outbox. It creates no semantic
   `durable_job` before the exact handler registry and matching feature flag are
   available. The later bounded materializer converts every flag-eligible
   unjoined intent to exactly one queued known-handler job, with at most one job
   per unique effect key. It does not silently skip stale input: the worker closes
   that job with a deterministic no-edge terminal record. Workers claim and
   execute only after commit, and no model work runs in the publication
   transaction. Projection-outbox acknowledgement is not intent consumption.
3. Bounded lexical/graph retrieval produces an allowlisted candidate set.
4. An LLM semantic judge may select only those IDs and returns a relation and
   direction combination from the supplied `relation_direction_policy`, a
   discrete relevance grade, and short verbatim
   source/target evidence quotes. The canonical service, not the model, locates
   those quotes in the immutable bodies and derives Unicode-codepoint spans;
   whitespace-only folding accommodates extractor line wrapping, while
   paraphrases and unanchored quotes fail closed. Independently repeated
   substantive claims, constraints, or procedures remain eligible for
   `related`; shared scaffolding or administrative metadata alone does not.
5. A deterministic validator verifies current revisions, anchored evidence,
   permissions, LCA, relation vocabulary, confidence threshold, duplicate/self
   exclusion, suppression, and effect-key idempotency.
6. Every passing edge activates automatically. Invalid, stale, malformed, or
   low-confidence candidates are recorded as non-active evaluation outcomes or
   discarded according to the frozen retention policy.

Before the first automatic-edge worker flag becomes effective, a keyset-
paginated, restartable baseline seed completes and reconciles every pre-existing
current active/published revision. A new
detector generation reseeds the same current corpus; an organization move seeds
only affected current revisions for newly eligible ancestor targets while old
and new ancestor generations are invalidated. Item/Wiki restore seeds only its
reactivated item or Wiki under a newly incremented source-lifecycle vector.
Intent provenance is exactly
`publication|baseline_backfill|org_retarget|detector_reseed|lifecycle_reactivate`.
Publication provenance binds its same-transaction transition/audit/outbox; the
non-publication kinds bind their own seed run, snapshot high-water, cursor/range,
and applicable organization/detector/restore event. None fabricates publication
proof. Direct publication plus
seed races converge through the deterministic semantic effect key, and all
scans/materialization run in bounded restartable batches without model work in a
write transaction.

The selected `automatic-edge-v1` work budget is one authorized
FTS query with at most 20 lexical candidates, at most 20 one-hop neighbors from
the top six lexical seeds, deterministic dedupe/pre-rank to 12 model candidates,
no two-hop expansion, and at most four activated targets per source revision and
scope job. Aggregate retrieval is one closure-filtered query, not one query per
descendant Part. The frozen deterministic components are
`B=0.65*lexical_rank + 0.20*one_hop_graph + 0.15*title_tag_coverage` and
`EdgeScore=0.30*B + 0.55*(model_grade/3) + 0.15*evidence_validity`, with each
component closed to `[0,1]` and quantized before tie-breaking. Initial activation
thresholds are score at least 0.70 with
grade at least 2 for `related|supports|extends|example_of`, and score at least
0.80 with grade 3 for `depends_on|contradicts`; held-out evaluation may change
them only through a reviewed contract update before rollout.

The active relation vocabulary is maintained as a separate, versioned list. It
is not inferred from free-form model text. Initial semantic types are
`related`, `supports`, `contradicts`, `depends_on`, `extends`, and
`example_of`; `references` is never a semantic type. `item-ref-v1` records the
deterministic direction from the containing source revision to the target item
in the separate reference ledger.

An edge records endpoint revisions, source/target evidence hashes/spans,
detector/model/prompt/scoring versions, confidence, LCA proof, job/effect key,
and creation generation. Endpoint edit, archive/deactivation, organization
move, or role/closure change hides or marks it `needs_revalidation` before reuse.

Only the exact local/home or break-glass authority in section 5.4 can suppress/
deactivate a wrong edge and record a reason. Suppression is durable and prevents
the same detector generation from recreating it. Automatic edge processing
never merges, replaces, deactivates, purges, or chooses a winner between
conflicting facts.

### 7.3 Explicit document references are a separate list

- An author inserts a stable explicit reference only as
  `[[item:<lowercase-uuid>]]`; the literal ASCII prefix/suffix and canonical
  lowercase UUID are syntax version `item-ref-v1`, and title-only resolution is
  forbidden.
- Explicit references are deterministic provenance/navigation records, not
  semantic `knowledge_edge` or `aggregate_knowledge_edge` rows. They appear in a
  separate `참조 문서` list and do not affect graph degree or semantic-edge
  confidence.
- The source revision, ordinal, target item, target revision observed at
  creation, source/target spaces, and authorization/closure proof are stored.
- Publication rejects a forged/concealed target without disclosing it. A higher
  native document may reference an item in its authorized descendant closure;
  a Part item cannot reference a sibling/foreign Part through a client ID.
- Target edits keep the stable item reference and show that a newer revision is
  available. Target deactivation/archive/permission loss hides content and
  marks the reference unavailable rather than silently retargeting it.
- Retrieval may include a bounded, authorized explicit reference as mandatory
  structured context, separate from scored full-corpus candidates. It never
  becomes a path for adding an unauthorized ID.

## 8. Upload-to-knowledge pipeline

The canonical path is:

```text
raw CAS staging
  -> rich extraction to canonical Markdown + child assets
  -> immutable preview
  -> explicit target canonical publication (initial curation state)
  -> bounded versioned document-curation scan
  -> automatic no-blocker release or review-required provisional state
  -> published-only FTS/node visibility
  -> asynchronous automatic-edge jobs
  -> asynchronous per-space projection
```

- Every detector whose frozen policy can produce `agent_hidden` or
  `publication_blocked` completes before first Agent exposure. An ordinary
  deterministic no-blocker result releases the revision automatically without
  a human click; strong near-duplicate, update, or potential-conflict content
  remains provisional until reviewed. Automatic-edge/projection work starts
  afterward and cannot mutate item curation status, FTS membership, or document
  visibility. Edge enrichment failure is not upload failure.
- PDF, DOCX, PPTX, XLSX, text, Markdown, and other accepted legacy formats use
  one versioned extractor contract. Embedded supported assets become canonical
  child assets rather than preview-only warnings.
- The Windows stager must work with the documented service-account NTFS
  `Modify` permission. It must not demand `FILE_DELETE_CHILD` merely to pin a
  root or create a CAS shard.
- File paths are never canonical knowledge identity. Raw and extracted bodies
  are hash-bound, no-follow verified, and revalidated before commit.
- SBE-8.13 authoring keeps raw files, exact extraction and generated Wiki bodies
  distinct. Publication source references remain extraction-authored, not model-
  invented. A ready authored candidate and its actual selected publication body
  are separate identities: for an exact ordinary reupload, reuse the first
  source-equivalent body in the authorized native target instead of duplicating
  a new model paraphrase. Retain fresh upload provenance. Both targets must share
  the selected initial body without synchronizing their later histories; incompatible
  existing target content requires explicit resolution. Replay binds the requested
  candidate in the request hash and verifies the actual selected body separately.
  A prior extraction-only publication is preserved as extraction, never labeled
  as authored merely because a later ready candidate exists. Its immutable reuse
  receipt binds the requested candidate, baseline extraction and baseline operation;
  fresh upload provenance still points to the new original/extraction. Equal-body
  mixed extraction/authored anchors follow earliest-publication order; different
  bodies require explicit resolution. Lost-ack replay uses immutable publication
  metadata rather than the anchor's later lifecycle. This internal path remains
  unactivated until durable authoring/reduction, lifecycle/retention, full
  source-race/load and real file/model gates pass.
- The legacy direct-file writer is a compatibility path only. New UI edits and
  uploads write canonical DB state; projection files are browse/download
  read-only.
- Internal authoring jobs derive actor, source and mutation authority from the
  server-owned completed upload, never an arbitrary client-selected job space.
  A retained chunk handler executes one ordinal and atomically commits its
  immutable result and job success. A restart may repeat a provider invocation
  whose response was never committed; do not advertise exactly-once external
  calls. Admission accounts for the product of durable and runner attempts.
  Failed/cancelled/expired work preserves extraction and cannot masquerade as
  a final reduced Wiki. A generic `authoring_incomplete` run disposition does
  not mislabel cancellation as provider failure; detailed job/audit reasons are
  separate. Model I/O remains outside DB transactions, with authority/source
  validation immediately before it and independently at commit.

## 9. Retrieval and reranking

### 9.1 Corpus boundary

- Retrieval accepts only a server-resolved `CorpusClosure`.
- A selected personal Wiki searches that Wiki only.
- `모든 내 Wiki 검색` is an explicit read-only management search, exposed as
  `POST /api/knowledge/v2/personal-wikis/search` and a `내 지식` header action.
  It searches all and only the caller's active Wikis using one owner/status-
  filtered bounded query and returns at most ten results with real `space_id`/
  revision provenance. It does not create or override a conversation, enter an
  Agent prompt, or search/report archived hits. Opening a result selects that
  exact Wiki and starts (or opens) a conversation bound to it. Thus every Agent
  conversation still has one authoritative Wiki; there is no per-turn override
  or per-Wiki fan-out.
- Team/Responsibility queries search the authorized aggregate closure. Results
  retain origin space and inherited/native labels.

### 9.2 Authorized full-corpus, byte-bounded pipeline

The SBE-8.13 target `canonical-hybrid-rerank-v4-shared-preview-budget` contract is
below. Its implementation remains gated on the work/quality evidence in
`evidence/SBE-8.13.md`; a version label is not activation approval.

1. one authorized FTS query over every lexical match in the selected closure,
   with no direct-candidate count truncation;
2. expand active one-hop neighbors from at most the top 12 seeds, at most four
   per seed and at most 40 unique neighbors;
3. deterministic dedupe/pre-rank over the complete authorized candidate union;
4. only when the stage-one sufficiency gate fails, expand two hops from at most
   the top five trusted anchors, with decay and at most 20 additional unique
   candidates;
5. retain every relevant unique ranked document and supply authorized current
   body evidence for corpus-wide/follow-up questions without a fixed document-
   count cap; never pad the ranked lane with irrelevant documents;
6. compose mandatory plus ranked/supplemental evidence into an exact 96 KiB
   shared budget. SBE-8.13 removes the per-document 4 KiB prefix cutoff:
   supply full authorized revision bodies when they fit, otherwise exact
   source passages with source size/hash and range/omission disclosure.
   Retrieval cache previews are not substituted for full body evidence.

Cycle guards, hub-degree penalties, hop decay, active/current-revision checks,
and global work budgets are mandatory. Query relevance and permanent-edge
confidence are different scores and caches.

There is no fixed document-count limit for the selected view's ranked or body-
evidence lanes. Explicitly pinned or stable-reference context remains separately
labeled and cannot be displaced, but is limited to four combined mandatory
documents. All model-bound excerpt text shares the 96 KiB budget; deterministic
priority, clipping, and coverage disclosure prevent an unbounded prompt.

SBE-8.13 citations bind only canonical bodies actually supplied in the current
accepted turn, never manifest titles or grader-only previews. The model selects
server-owned turn/view/source/item/revision handles beside chapter evidence;
the server validates markers and constructs exact selected-view original-file
URLs after generation. Historical reference targets retain as-of-revision
artifact provenance. Titles and filenames are display labels, not authority.
Persisted provenance distinguishes supplied sources from actually cited ones;
structural link validity does not establish semantic chapter support. General
knowledge, external tools and uncited answers remain possible. Download-time
authorization/retention verification is unchanged. Browser rendering, lifecycle
and held-out semantic evidence remain activation gates for this packet.

The deterministic rank uses lexical rank, title/tag coverage, graph path/type,
edge validity/confidence, hop and hub penalties, source-scope affinity, and
stable tie-breakers. Freshness affects rank materially only when the query has
temporal intent.

An optional local multilingual cross-encoder may reorder only the authorized
candidate set. It cannot add IDs, change the closure, displace explicit pins, or
become a correctness dependency. If its own 128-KiB serialized input bound is
exceeded, or on timeout, OOM, unavailable model, malformed output, or provider
failure, it returns the exact deterministic `ranked_documents[]` core for
the same canonical input: item/revision IDs, order, deterministic scores, and
evidence serialize identically across failure causes and retries. The outer
response may report a bounded `reranker_status`/reason and is not claimed to be
byte-identical to a successful reranked envelope. The company API is not
required; Codex bridge/GGUF may validate the provider interface in the isolated
environment.

Temporal intent and typed temporal features remain deterministic and are not
inserted into cross-encoder text. The `hybrid-rerank-v4-shared-preview-budget` fusion is
`0.85 * deterministic_score + 0.15 * discrete_model_grade`; exact weights and
tie rules are versioned. The answering model receives typed time metadata only
after authorization/final selection. Thus cross-encoder failure cannot change
the meaning or availability of freshness evidence.

SBE-8.13 ranking previews are distinct from final-answer body evidence. Direct
matches use query-centered FTS snippets; graph-only passages are query-guided
or explicitly distributed samples when lexical overlap is absent. Preserve all
authorized candidate identities and graph scoring semantics. Deduplicate source
work and bound total source scanning as well as the complete serialized grader
input; a per-preview text bound alone does not satisfy the global work budget.

The current implementation plans distinct surviving graph-only revisions after
both-hop merge, relevance filtering and exact-content dedupe. With optional
local grading enabled, it reads them in one batch only if their canonical byte
sum fits 8 MiB; otherwise it reads none for grading and retains the complete
deterministic candidate set. The runtime does not prepare graph body previews
when local grading is disabled. Empty/unprepared ranking previews are not empty
canonical documents: final-answer body materialization still loads the current
authorized bodies independently. These are internal work counters/policy flags,
not permission or document-count limits. The raw-source cap does not bound
SQLite/FTS work or process RSS.

Grader input v3 reserves every identity/title/query, then allocates JSON-escaped
excerpt bytes with deterministic max-min fairness and query-guided exact passage
selection. Short previews release unused allowance. The complete canonical JSON
must fit 128 KiB; metadata overflow or an inability to represent a nonempty
source preview skips the entire optional call. No candidate identity is removed.
The 2,048-token grader output setting still requires an explicit large-candidate
output test; input fit alone does not establish successful grading of a large
set. Code tests and diagnostic timing do not close held-out model-quality or
target-server load gates.

## 10. Per-space Index, Log, Graph, and projections

- Every personal Wiki and native Part/Team/Responsibility space has its own
  Index, Log, Graph, backlinks, and generation identity.
- A higher aggregate view materializes metadata/graph composition from its
  closure and generation vector; it does not copy descendant Markdown bodies
  into the higher native space.
- Projection work is lazy and target-filtered. A change schedules the affected
  physical space and ancestors whose aggregate generation depends on it, not
  every Wiki in the system.
- Old immutable generations are garbage-collected under retention and backup
  policy. The current generation is reconstructible from canonical rows.

## 11. Markdown temporal footer and freshness

The page footer summarizes the current snapshot; `log.md` remains the complete
history. The footer is presentation metadata, never canonical body text.

| UI label | Canonical source | Presence |
|---|---|---|
| 생성 | `knowledge_item.created_at` | required |
| 마지막 내용 수정 | current `knowledge_revision.created_at` | required |
| Revision | current positive integer `knowledge_revision.revision_no` | required |
| 원문 수정 | active trusted source temporal assertion | only with current evidence-backed value |
| 정보 기준 | active explicit effective-date assertion | only with current evidence-backed value |
| 마지막 검증 | current attestation bound to the revision and temporal-summary hash | only while current |

- `knowledge_item.updated_at`, blob verification time, finding review time, and
  projection build time must not masquerade as content modification/fact
  verification.
- DB instants are UTC epoch milliseconds. API temporal values are a tagged
  union: an instant is ISO-8601 UTC and a date-only fact remains `YYYY-MM-DD`
  with `precision=date`; it is never fabricated as midnight. Korean UI/footer
  renders instants with IANA `Asia/Seoul` and an explicit `+09:00` offset.
- An absent, revoked, superseded, or non-current optional value omits its entire
  footer row; the footer never emits `미지정` or `미검증`. File mtime and upload
  time are never guessed as information-effective time.
- A new revision inherits neither temporal assertions nor verification
  automatically. An adapter may explicitly assert the same source fact for the
  new revision with fresh evidence. Temporal and verification event streams are
  append-only and update a CAS-protected head; revoking a head leaves no active
  value and never silently falls back to a superseded value.
- Verification binds the exact revision plus the current temporal-summary hash.
  Changing an effective/source-time head removes the `마지막 검증` row until
  the new summary is separately attested.
- `temporal_summary_sha256` is SHA-256 of canonical JSON with exact keys
  `{schema_version, revision_id, source_modified_head,
  information_effective_head}`. Each head contains its event ID/state and tagged
  typed value or an explicit revoked/null state. Verification/audit/footer/
  attestation fields are excluded so the digest is not self-referential.
- `last_verified_at` is the DB-assigned attestation commit time, ordered by the
  stream's monotonic sequence. An optional claimed external verification time is
  provenance only and never selects the current head or replaces this label.
- Footer bytes are excluded from canonical raw/normalized hashes, FTS, exact or
  normalized dedupe, embeddings, semantic-edge input, reranker text, and Agent
  excerpts. Typed temporal fields may accompany a result separately.
- Projection manifest v2 records the exact LF-normalized UTF-8 prefix byte
  length/hash, footer schema, and rendered length/hash separately from raw
  source hashes. Re-import strips a footer only through an exact trusted
  manifest/body-boundary proof; marker guessing is forbidden.
- Rebuilding the same canonical snapshot is byte-identical. A current clock is
  never embedded as content modification time.
- A projection footer prints the effective date/value only; it never computes a
  wall-clock-relative `scheduled/current/expired` label. UI/ranking may derive a
  relative status from an explicit request `as_of` instant.
- Newer is not automatically more authoritative. Temporal boost is enabled for
  temporal questions; conflicts and source authority remain explicit.

Temporal-only events increment a per-space temporal generation, enqueue one
native projection event, and invalidate current ancestors' aggregate generation
vectors. They do not change content/FTS/embedding generation. A rendered
projection generation binds content, graph, and temporal generations, so a
footer update creates a new immutable projection rather than overwriting an old
one.

`snapshot_as_of_at` in a projection comes from the pinned canonical domain/audit
high-water used to build that generation, not the projector clock. Worker
`built_at` is operational telemetry stored outside immutable manifest/rendered
bytes and their hashes. Rebuilding the same high-water therefore produces the
same manifest and files even at a later wall-clock time.

A manifest is trusted for footer removal only when either (a) its exact
generation/manifest digest is anchored in the same canonical projection ledger
and the bound projection root passes identity checks, or (b) a portable export
envelope verifies under an approved deployment signing-key ID. A self-declared
hash or adjacent user-supplied manifest is not a trust root. Without that proof,
footer-like bytes remain user content.

Temporal mutation authority follows native write authority:

- a personal owner may assert/attest/revoke current revisions in that Wiki;
- an active Part editor may do so for a Part-native revision;
- the exact Team leader or 담당 executive episode may do so only for that native
  higher-space revision;
- an ancestor reader cannot attest an inherited descendant item, and system/LLM
  checks are separately labeled diagnostics that never produce user-facing
  `검증됨`.

Every temporal event commits audit, outbox, head CAS, temporal generation, and
projection invalidation together. Per-Wiki Log renders assert, supersede,
revoke, attest, and verification-revoke actions once in deterministic
`(committed_at, audit_id)` order.

The projection v2 footer byte grammar is frozen, not locale-free-form:

```text
<exact markdown_lf_v1 UTF-8 body prefix>\n\n<!-- knowledge-footer:v2 -->\n---\n> **문서 정보**  \n> 생성: <KST instant>  \n> 마지막 내용 수정: <KST instant>  \n> Revision: <positive decimal revision_no>  \n<SOURCE_ROW><EFFECTIVE_ROW><VERIFICATION_ROW><!-- /knowledge-footer:v2 -->\n
```

```text
SOURCE_ROW       := "" | "> 원문 수정: <KST instant | date>  \n"
EFFECTIVE_ROW    := "" | "> 정보 기준: <KST instant | date>  \n"
VERIFICATION_ROW := "" | "> 마지막 검증: <KST instant>  \n"
```

The metavariables above are specification only and are not emitted. Optional
rows appear only when their current evidence-backed heads exist and always in
source/effective/verification order. The two leading LFs and final LF are
always present even when the body already ends in LF; field order/labels/UTF-8/
LF/escaping are schema-versioned. All values come from validated typed metadata,
never user-controlled footer text. Exact golden bytes are registered unchanged
in `SBE-0.1`.

For migrated knowledge, `생성` and `마지막 내용 수정` mean the canonical Wiki
import/revision event times. They do not claim to be the legacy source's author
or modification time. A separately verified legacy/Notion timestamp may appear
only as `원문 수정`; migration time and `system_import` never become
source-modified or information-effective evidence by themselves.

## 12. Operational and provider boundary

- No product-level Wiki-count cap is introduced. Capacity is protected through
  bounded cursor pages, queries, job queues, upload/storage quotas, projection
  scheduling, and per-request time/token budgets.
- Required test scales include 1, 100, and 1,000 personal Wikis for one owner,
  plus the synthetic multi-Part organization corpus.
- 10111 remains an isolated development preview until `SBE-7.2`. It must never
  share mutable roots or canonical DB state with 10011/production.
- Local/Codex bridge validation may prove prompt schemas, fallbacks, and
  deterministic commit validation. It cannot substitute for later company-model
  compatibility, target-server load, multi-client, backup/restore, or operator
  acceptance evidence.
