# SBE-2.3 evidence

Status: VERIFIED

This record closes `SBE-2.3 - CorpusClosure and higher authority`. It does not
authorize feature-flag activation, a live migration, production traffic,
company-provider calls, or the v12 temporal/footer work.

## 1. Result

- Started: 2026-08-24 16:37 Asia/Seoul.
- Verified: 2026-08-24 Asia/Seoul.
- User decision: the user explicitly approved `v11 authority / v12 temporal`
  on 2026-08-24 after the frozen-v10 representation conflict was recorded.
- Physical result: canonical v11 is the forward-only schema head. Versions
  v1-v10 remain byte-identical; v11 belongs to SBE-2.3 and v12 remains assigned
  to later temporal assertion/verification/projection-footer work.
- Acceptance result: `HSPACE-006..014` are proven.
- Runtime result: all successor traffic flags remain disabled by default.

## 2. Delivered boundary

The packet now provides the following complete local implementation:

- Typed `AccessPlan` and `CorpusClosure` for personal Wiki, Part, Team,
  Responsibility, and revision-bound distribution overlays.
- Origin-preserving aggregate reads and management/Agent response metadata.
- Team-leader and Responsibility-executive role episodes with exact assignment
  generation, row-version, actor, space, closure, and organization fences.
- Explicit downward distribution without copying item bodies.
- Organization-move invalidation of closures, grants, durable jobs, caches, and
  aggregate projection generations.
- Exact higher-role lease, immutable edit, lifecycle, and review authority.
- One explicit Team or Responsibility native publication target from preview,
  through upload extraction job, commit, immutable operation history, and
  replay.
- Exact current Team leader or Responsibility executive curation authority;
  ordinary members and unrelated administrators receive no higher authorship.

## 3. Acceptance proof

| ID | Verified outcome |
|---|---|
| `HSPACE-006` | Personal exact-Wiki closure and Part native plus fixed distribution overlay are deterministic. |
| `HSPACE-007` | Team closure contains Team native plus all and only active descendant Parts. |
| `HSPACE-008` | Responsibility closure contains Responsibility native, descendant Teams, and descendant Parts. |
| `HSPACE-009` | Descendant readers can consume ancestor aggregate views but cannot mutate them; exact current Team leader and Responsibility executive can publish to their own native space. |
| `HSPACE-010` | Publish, curate, edit, lifecycle, and lease capabilities are admitted only for the exact current higher-role episode; member/admin nonholders are denied or concealed. |
| `HSPACE-011` | Role replacement, role end plus write disable, scope/closure/organization drift, failpoint retry, replay, durable jobs, and two-process contention are fenced. |
| `HSPACE-012` | Retrieval, management, finding, and Agent responses preserve source/view/origin/capability/closure metadata and conceal foreign identifiers. |
| `HSPACE-013` | Distribution is revision-bound and read-only; ancestry and role authority are exact; confirmed administrator revoke is the only break-glass path. |
| `HSPACE-014` | Organization move atomically invalidates former/new closure, distribution, job, cache, and projection generations. |

An organization move cannot occur while an affected higher role is open. The
verified sequence is therefore: close the exact role and disable its native
space, which invalidates any uncommitted publication authority; then execute
the approved move, which invalidates the old/new organization-derived closure
state atomically. This prevents a stale pre-move role episode from surviving as
a usable publication or curation credential.

## 4. Exact higher-publication authority flow

1. The preview request supplies one closed target descriptor containing the
   Team/Responsibility native `space_id`, target kind, `publish` capability,
   role-assignment ID, and role-assignment generation.
2. `ScopeResolver` resolves the actor's current `AccessPlan`. The selected
   native mutation space and exact current role episode must match the target.
3. The preview transaction stores one immutable
   `higher_publication_preview_authority` row. It captures actor scope/row
   versions, role row/generation, closure hash/generation, organization
   snapshot version, descriptor JSON/hash, and target space identity.
4. Upload previews put the same identity into the durable extraction job's
   authorization fence; a stale worker cannot substitute a target or role.
5. Commit re-resolves current authority and compares every captured fence. Any
   role replacement/end, write disable, account/scope change, closure drift, or
   organization drift rejects the uncommitted preview without domain effects.
6. A successful transaction creates exactly one publication operation, one
   matching publication target, and one immutable
   `higher_publication_operation_authority` row. Audit/outbox/domain effects
   commit together.
7. A same-operation retry returns the exact stored result. Semantic drift or a
   mismatched descriptor is rejected; concurrent processes converge on one
   operation and one item.

Legacy `personal|part|both` request hashes, target semantics, stored rows, and
responses remain unchanged. `explicit` is additive and requires exactly one
higher target descriptor.

## 5. v11 migration and catalog integrity

- Migration: `knowledge_core/migrations/v011_higher_publication_authority.py`.
- New immutable tables:
  `higher_publication_preview_authority` and
  `higher_publication_operation_authority`.
- `publication_operation` is rebuilt forward-only to add
  `target_mode='explicit'` and `explicit_authority_id`; every historical row is
  copied in stable order with `explicit_authority_id=NULL`.
- Preview, operation, target, and finding-review triggers accept the additive
  exact-authority form while retaining legacy validation.
- Canonical v11 shape: 67 tables, 178 indexes, and 194 triggers.
- Expected schema SHA-256:
  `1638cc6090a60fe1680b67fe6a6394d264b2cd596156bc4062b88948ec75b90a`.
- v11 migration checksum:
  `d584077e9e774129998b154ddc0b056c2db621ee07ea418185352b1d4b494238`.
- Canonical catalog checksum:
  `280d1f03ce6e1127d8771cf489c5df393fbcb73bf73bc59498e9fce818c04f82`.

The migration tests prove all of the following:

- the frozen v1-v10 catalog checksums are unchanged;
- a real legacy personal publication created on populated v10 survives the
  v10-to-v11 upgrade byte-for-byte and replays exactly;
- pristine v1-to-v11 and populated v10-to-v11 produce the same schema;
- an injected v11 failure rolls back to the exact v10 schema;
- a second v11 migration run is a no-op.

No temporal assertion, verification, freshness, or projection-footer behavior
is implemented here. That work remains owned by v12.

## 6. Curation authority

The `space_editor` finding-review branch now additionally accepts only:

- the exact open `team_leader` episode for the finding's Team native space; or
- the exact open `responsibility_executive` episode for the finding's
  Responsibility native space.

The application service still resolves `Capability.CURATE`, and the SQLite
trigger independently verifies the physical space, organization kind, role,
assignee, assignment generation, open episode, active organization, active
write-enabled space, and active actor. System administrator status alone does
not grant this authority.

## 7. Conflict resolution record

The original in-progress audit correctly stopped because frozen v10 allowed
only `personal|part|both` publication modes and could not represent exact
Team/Responsibility publication history or higher-role curation honestly. The
following alternatives remained rejected:

| Rejected alternative | Reason |
|---|---|
| Edit or re-freeze v9/v10 | Would invalidate completed migration and evidence immutability. |
| Store Team/Responsibility as `part` | Would falsify physical scope and replay provenance. |
| Treat admin or Part leader as the reviewer | Would grant authority to the wrong principal. |
| Insert higher items outside publication history | Would lose canonical idempotency, audit, outbox, and replay. |
| Bundle temporal behavior into v11 | Would violate packet dependency order and prematurely implement SBE-3.2/SBE-5.2. |

The approved resolution preserved v1-v10, assigned higher authority to v11,
and moved the unchanged temporal reservation to v12.

## 8. Verification gates

Focused exact-authority proof after the final role-disable and administrator
nonholder cases were added:

```text
..\venv\Scripts\python.exe -m pytest -q \
  tests\knowledge_spaces\unit\test_higher_publication_service.py
11 passed in 16.88s

..\venv\Scripts\python.exe -m pytest -q \
  tests\knowledge_spaces\unit\test_higher_space_routes.py
3 passed in 1.69s
```

The complete affected authority, scope, publication, finding, lifecycle,
organization, migration, schema, and contract regression aggregate passed:

```text
..\venv\Scripts\python.exe -m pytest -q \
  tests\knowledge_spaces\unit\test_higher_authority_service.py \
  tests\knowledge_spaces\unit\test_scope_resolver.py \
  tests\knowledge_spaces\unit\test_knowledge_core_composition.py \
  tests\knowledge_spaces\unit\test_runtime_preflight.py \
  tests\knowledge_spaces\unit\test_publication_preview_repository.py \
  tests\knowledge_spaces\unit\test_publication_preview_service.py \
  tests\knowledge_spaces\unit\test_publication_service.py \
  tests\knowledge_spaces\unit\test_higher_publication_service.py \
  tests\knowledge_spaces\unit\test_publication_routes.py \
  tests\knowledge_spaces\unit\test_versioned_finding_service.py \
  tests\knowledge_spaces\unit\test_item_edit_service.py \
  tests\knowledge_spaces\unit\test_item_lifecycle_service.py \
  tests\knowledge_spaces\unit\test_lease_service.py \
  tests\knowledge_spaces\unit\test_durable_work_kernel.py \
  tests\knowledge_spaces\unit\test_organization_repository.py \
  tests\knowledge_spaces\unit\test_organization_service.py \
  tests\knowledge_spaces\unit\test_migration_v9_higher_native_spaces.py \
  tests\knowledge_spaces\unit\test_migration_v10_semantic_graph_contracts.py \
  tests\knowledge_spaces\unit\test_migration_v11_higher_publication_authority.py \
  tests\knowledge_spaces\unit\test_schema_v1_catalog.py \
  tests\knowledge_spaces\unit\test_contract_audit.py
508 passed in 615.07s
```

The migration/catalog-focused selection independently passed 14/14, and the
post-transition contract-audit unit suite passed 27/27 in 80.56s. The final
changed Python modules/tests also passed `py_compile`.

The final direct report-schema-v2 audit after the VERIFIED status/backlog
transition passed with zero errors:

```text
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.contract_audit audit \
  --docs-root G:\AI_Agent\DEMOS_CODEX\code_assist_v1\docs\knowledge_spaces \
  --output -
exit 0; status pass; error_count 0
input_sha256 7ea2fd2074ce6fb69e62613d85ae18734bfe27c1b59a403a3792aff8e842d594
41 requirements; 20 goals; 284 scenarios; 69 paths; 75 operations;
31 stable errors; 12 schemas; 12 valid examples
```

## 9. Boundary attestation and handoff

- No live database/root, production service, target port, feature flag,
  company API, provider, or model was accessed or changed.
- No personal, Part, Team, or Responsibility write traffic was enabled.
- No v1-v10 migration or completed historical evidence was modified.
- v12 temporal/footer behavior remains unimplemented and unclaimed.
- With SBE-2.1 and SBE-2.3 verified, the next runnable packet is SBE-2.2:
  conversation-Wiki binding and archived-history restoration.
