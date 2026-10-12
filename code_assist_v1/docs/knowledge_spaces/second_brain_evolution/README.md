# Second Brain evolution package

Status: successor machine contract registered by SBE-0.1; runtime implementation
starts at SBE-1.1 and every successor flag remains false.

This package is the execution contract for evolving the canonical-v7 Knowledge
Core into the multi-Wiki, organization-aware Second Brain agreed with the user
on 2026-08-23. It is deliberately separate from the verified P0-P7 evidence.
The old evidence remains true for the behavior that was gated at that time; it
does not prove this successor behavior.

## Authority and transition rule

Two truths must be kept separate until the forward migrations and gates pass:

- **Current implementation:** canonical v7, one personal space per user,
  personal/current-Part scope, higher scopes as limited read-only preview views,
  and human-approved semantic edges.
- **Approved target:** unlimited personal Wikis, native Team/Responsibility
  spaces, descendant aggregate views, automatically activated validated
  semantic edges, and authorized full-corpus ranked/body retrieval with an exact
  96 KiB model-excerpt budget and separately max-four mandatory pin/reference
  lane.

The approved target in this directory supersedes ADR-005/ADR-014 only where the
ledger says so, and supersedes only ADR-009's human-click requirement for a
validated semantic edge. ADR-009 document curation visibility and every
destructive/conflict-review boundary remain active. This package does not by
itself authorize a schema change, feature flag, live migration, production
traffic, or company API call. `SBE-0.1` must first synchronize the machine-
readable contracts and register the exact forward schema. Each later behavior
becomes authoritative only after its packet is `VERIFIED` and its feature gate
is explicitly enabled.

Never edit old migration sources or completed `evidence/P*.md` files to make the
past look as if it implemented the new target.

## Required reading order

An implementation Agent must read:

1. the repository-root `AGENTS.md`;
2. this file;
3. `PRODUCT_CONTRACT.md`;
4. `FORWARD_SCHEMA.md`;
5. `EXECUTION_PLAN.md`;
6. `ACCEPTANCE.md`;
7. the existing Knowledge Spaces documents in the order listed by
   `AGENTS.md`;
8. `STATUS.md` and the evidence file for every prerequisite packet.

When the current-v7 documents and this package differ, use this package only
for the target behavior owned by an active SBE packet. Preserve every unrelated
v7 invariant.

## Document map

| Document | Purpose |
|---|---|
| `PRODUCT_CONTRACT.md` | User-approved behavior, permissions, lifecycle, retrieval, graph, upload, and UI invariants |
| `FORWARD_SCHEMA.md` | Forward-only v8-v13 logical schema and migration/postcondition requirements |
| `EXECUTION_PLAN.md` | Dependency graph, packet ownership, implementation sequence, gates, and handoff protocol |
| `ACCEPTANCE.md` | New acceptance IDs, negative tests, performance bounds, and release criteria |
| `../evidence/SBE_TEMPLATE.md` | Required evidence format for each packet |

## Supersession ledger

| Existing rule | Approved successor |
|---|---|
| One personal space per user | Any number of personal Wikis; exactly one active default |
| A conversation is bound to one physical space and switching the selector creates a new conversation | Still true; a higher selected view additionally resolves to a server-owned multi-space `CorpusClosure` |
| Higher scopes alias only the actor's current Part | Team includes its native space and all descendant Parts; Responsibility includes its native space, descendant Team native spaces, and all descendant Parts |
| Team/Responsibility cannot write | Their exact current leader/executive may write native higher-space documents |
| `API_UI_CONTRACT.md` section 9 lets administrator status supply ordinary Part authorship/curation | Administrator status alone supplies no ordinary authorship; an administrator may mutate ordinary content only through independently held owner/membership/role authority, while the exact break-glass matrix remains separate |
| Semantic-edge activation requires human approval in current v7 | Every edge candidate that passes the deterministic automatic-edge contract activates without a click; document update/merge/conflict curation remains under ADR-009 |
| Graph edges are same-space only | Existing `knowledge_edge` remains same-space; a new aggregate edge ledger stores cross-space edges at their lowest common organizational ancestor |
| Retrieval is one-space FTS plus direct neighbors | Retrieval runs one lexical plan over the complete authorized personal/Part/Team/Responsibility closure, applies bounded graph expansion, and has no fixed document-count cap in its ranked/body-evidence lanes; up to four authorized mandatory pin/reference documents share the exact 96 KiB model-excerpt budget with those lanes |
| Projection Markdown equals the canonical body | Canonical body remains unchanged; presentation Markdown may append a deterministic metadata footer with a manifest-bound body boundary |

## Global non-goals

- No automatic synchronization between personal and Part copies.
- No personal document is implicitly included in Part, Team, or Responsibility
  corpora.
- No semantic edge automatically merges, replaces, deactivates, purges, or
  declares a conflict winner.
- No Wiki-count-proportional query, worker, or projection fan-out.
- No company API is required for deterministic correctness. Provider-specific
  model quality and target-server capacity remain separate handoff gates.
- No live source root, live user DB, production flag, or production port is
  changed by the planning package.

## Packet states

Only these states are valid:

- `NOT_STARTED`
- `IN_PROGRESS`
- `IMPLEMENTED_NOT_GATED`
- `VERIFIED`
- `BLOCKED`
- `DEFERRED`

`VERIFIED` means the implementation, focused tests, adjacent regressions,
contract audit, evidence file, hashes, and residual handoff all exist. Code that
"works locally" without that evidence is `IMPLEMENTED_NOT_GATED`.

## First executable action

The first packet is `SBE-0.1`, not P7.8, P7.9, or P8. It updates the human and
machine contracts together, assigns the new acceptance IDs, freezes the exact
DDL/API shapes, and records a new baseline. Target load, restore, and cutover
evidence must be collected after the successor behavior stabilizes; otherwise
those expensive measurements would immediately become obsolete.
