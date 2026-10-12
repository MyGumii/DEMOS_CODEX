# SBE-2.2 evidence

Status: VERIFIED

## 1. Result

- Packet: `SBE-2.2 - conversation-Wiki binding and archived history`.
- Acceptance: `WIKI-013..017`.
- Completed: 2026-08-24 Asia/Seoul.
- Outcome: every successor conversation is immutably bound to the exact
  server-authorized personal Wiki or organization view stored in
  `conversation.space_id`. Each accepted turn carries a canonical actor,
  selected-view, role, lifecycle, organization, and corpus-closure fence that
  is checked before retrieval and again before final commit.
- Archived personal-Wiki sessions remain owner-readable history, reject normal
  mutations and new turns with `WIKI_ARCHIVED_HISTORY_ONLY`, and resume on the
  same conversation only after explicit restore and current authorization.
- Gate conclusion: focused service/API, cross-boundary, operation/preflight,
  schema-compatibility, compile, and report-v2 contract gates pass. The
  successor flag remains disabled.

## 2. Boundary and no-go attestation

- Owned scope: conversation repository/service, scope selector, Agent context
  and execution payload, canonical route/error response, runtime composition,
  the disabled conversation-binding flag, API schema, tests, and contract text.
- No migration or DDL was added. Canonical schema remains immutable v11; v12
  temporal/footer work was not started by this packet.
- Live `knowledge/`, `user_data/`, `.runtime`, Notion data, production DB,
  services, ports, and feature-flag values were not opened or changed.
- No company API, network model, or external provider call was made.
- Existing flag-off v1 conversation payloads and current-v7 behavior remain
  unchanged; successor-only fields are emitted only when the new flag is on.

## 3. Implemented contract

### 3.1 Exact creation and reopen binding

- `ConversationService.create()` resolves the requested ID through the same
  canonical `ScopeResolver`/`AccessPlan` used by higher views before insert.
  Foreign, forged, inactive, and archived-normal-mode IDs fail closed.
- `conversation.space_id` remains the only authoritative selected Wiki/view.
  It has no update API and remains protected by the existing immutable trigger.
- With successor binding enabled, `ScopeSelectionService` ignores conflicting
  browser/dropdown selection for an existing conversation and selects the
  stored ID. An active non-default Wiki is added as one exact bounded option;
  an archived stored Wiki is added as a zero-capability history-only option.
- Personal labels come from the exact `personal_wiki_profile.display_name`, not
  the legacy hard-coded default name.

### 3.2 Accepted-turn fence and cache identity

- `ConversationBindingFence` is strict canonical data containing actor ID,
  scope epoch and actor row version; exact selected view ID/kind; corpus closure
  generation and SHA-256; organization snapshot version; personal-Wiki
  lifecycle generation; and higher role assignment ID/generation where present.
- The accepted event stores both the complete fence and its canonical SHA-256
  cache identity. Client input cannot supply either value.
- `AgentTurnContextService` reloads the accepted event, verifies its exact
  shape/digest, re-resolves the current `AccessPlan`, and compares the complete
  fence before any retrieval call. It repeats the snapshot comparison after
  external context resolution.
- `ConversationService.complete_turn()` performs the same current-fence check
  before marking running or committing an assistant message/final response.
- The model adapter receives the selected view, closure generation/hash, and
  binding cache identity only in successor mode. Role replacement/end,
  membership/actor changes, organization/space closure changes, and
  archive-restore ABA lifecycle changes therefore cannot reuse stale retrieval
  or model-cache identity.

### 3.3 History-only and state isolation

- Repository reads for messages, summaries, pins, explicit contexts,
  reconnect, and conversation detail admit either ordinary accessibility or the
  exact owner's archived-Wiki history. Writes still require ordinary access.
- Service mutation boundaries translate archived-Wiki attempts to the stable
  `WIKI_ARCHIVED_HISTORY_ONLY` error before external context work or mutation.
  Terminal bookkeeping/reconnect remains possible for an already accepted turn.
- An exact replay of an already committed operation remains effect-free and can
  return its stored result from archived history; semantic drift or a new
  mutation remains rejected.
- Switching a Wiki/view still requires a new conversation ID. Messages,
  summaries, fixed-revision pins, explicit contexts, final citations, and the
  closure/cache digest stay with the old conversation.

## 4. Feature and API integration

- `KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING` is parsed, preflighted, reported,
  and present as `0` in the protected production example. It requires
  `KNOWLEDGE_CORE_MULTI_WIKI_READS`; that parent already requires installed
  multi-Wiki schema and canonical personal reads.
- Runtime composition creates one shared `ConversationRepository` with matching
  higher-view and Wiki-binding policy and injects the same `ScopeResolver` into
  conversation, selector, retrieval, and Agent-context services. Mismatched
  custom repository/Agent binding configuration fails construction.
- Conversation schema now admits `personal|part|team|responsibility` and the
  additive successor-only `history_only`, `access_status`, selected-view, and
  Wiki lifecycle-generation fields.
- Canonical archived detail returns HTTP 200 with `history_only: true`; a normal
  archived turn returns HTTP 409 `WIKI_ARCHIVED_HISTORY_ONLY`. A stale in-flight
  turn is terminated with the preserved `CORPUS_CLOSURE_STALE` code rather than
  a generic execution label.

## 5. Stable implementation inventory

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded to
avoid recursive documentation hashes. The 20-file inventory is 732,439 bytes.
Its sorted `path NUL size NUL sha256 LF` SHA-256 is
`2ae63b3d4ec6bd102994f51475ec35baf7290e0bfe181923a41f8082366a5588`.

| File | Purpose | SHA-256 |
|---|---|---|
| `knowledge_core/conversation_binding.py` | Strict immutable turn fence/cache identity | `627a285d89bef6db5a6d4d85eb1268ef16691da586ad78c92ff54b74cac0f6a5` |
| `knowledge_core/domain.py` | Disabled feature snapshot | `db5077bcfbf77b7bc85b9a68a6685bdec07968a9563a40a35e419a89b267d739` |
| `knowledge_core/feature_flags.py` | Exact prerequisite parsing | `e67fd5015ccbe04025a4f5ff3a037dfab6ae2a5e7c06a1090e2b13f09d854c88` |
| `knowledge_core/scope.py` | Active Wiki lifecycle in `AccessPlan`/closure | `c2c61bd3f7b5b8bd5ed58661fe66e0a37a4d8365a91a66a126a8ef206f2bf8cf` |
| `knowledge_core/repositories/conversations.py` | Active-write/history-read split | `714bde52f1d102ab19ad8a6f3aae5776525688d855ff924987fcc38a7d21c8c9` |
| `knowledge_core/services/scope_selection.py` | Stored-session selection authority | `e60970bc4b0111bbd775250f7585efb863b81ba2a07abcb827d4c114ad493338` |
| `knowledge_core/services/conversations.py` | Create/accept/complete/replay fences | `160266d6f0af16550f1b9ca670e5272dd33f5b93905d8047b5d686a8b06e4d4c` |
| `knowledge_core/services/agent_context.py` | Pre-retrieval and post-resolution validation | `4e2db48fd2d1a4c74121bd3193c021c68642f547adbb629d8ac7e3bba7a2cf15` |
| `knowledge_core/agent_runtime.py` | Shared flag-matched composition | `759c02eb2561fb8ebac6c8df61864be10fb829b6361ef08085e1998636c0fe2f` |
| `knowledge_core/agent_execution.py` | Binding identity in successor model payload | `fccdc73b77645559265fcfcb820995048a383e2e3ce62e965200d017689d3ed4` |
| `knowledge_core/canonical_routes.py` | Stable archived/stale route behavior | `0ce72cef42dee682350b036f6e601c301c27781181f0edebedef0c570d460f0e` |
| `tools/runtime_preflight.py` | Exact 13-flag inventory | `10452e77cc483bd07cf0d6111d7c4989ab10f77fe32dd7dc59768bf905ccbcdb` |
| `ops/production.env.example` | Disabled production default | `9b1a1058bf71a64af776c682c8c1bfa0beaa0ce01095a4a9b5292c7dc1652b09` |
| `docs/knowledge_spaces/API_UI_CONTRACT.md` | Authoritative session/fence behavior | `77780e7c155ff961a8456d5f8718727473d38827ba95cf8b350a6e96d1f08bdb` |
| `docs/knowledge_spaces/contracts/schemas/conversation.schema.json` | Additive API schema | `01024bfda9aa7d0d7192e988358165e4596c3d6e8056a34a417a4335de9f9dcf` |
| `tests/knowledge_spaces/unit/test_conversation_wiki_binding_service.py` | WIKI-013..017 service/API proof | `7e8fca3f38435404dc8e6f90bfd65ad12a965311f02dfa3bb3fab28d767b2b3c` |

The remaining four inventory members are the updated composition, runtime
preflight CLI, protected-environment, and production-service regression tests.

## 6. Commands and results

- Working directory: `G:\AI_Agent\DEMOS_CODEX\code_assist_v1`.
- Runtime: CPython 3.14.3, SQLite 3.50.4, Windows.
- Production feature flags: unchanged and disabled.

| Selection | Result | Duration |
|---|---:|---:|
| Final WIKI-013..017 dedicated service/higher/API suite | 5 passed | 10.27 s |
| Final conversation/repository/context/retrieval suite | 59 passed | 84.15 s |
| Affected conversation, Agent, Wiki, higher-authority, composition, and production aggregate | 259 passed | 178.49 s |
| Feature/composition contract | 50 passed | 20.06 s |
| Runtime-preflight and production-operational focus | 27 passed | 13.47 s |
| Schema catalog plus v10/v11 migration invariants | 14 passed | 16.57 s |
| Owned Python compilation | PASS | 1.87 s |

## 7. Acceptance mapping

| ID | Direct evidence | Observed |
|---|---|---|
| `WIKI-013` | `test_exact_authorized_wiki_is_bound_and_saved_session_overrides_client_state`; canonical API test | Exact active non-default Wiki accepted; foreign/forged and archived normal-mode IDs rejected; accepted event has server fence |
| `WIKI-014` | same selector test | Conflicting selected/default browser value cannot override stored session Wiki; exact profile name and one selected option returned |
| `WIKI-015` | `test_switching_wiki_creates_isolated_conversation_state_and_cache_identity` | Two immutable conversations retain distinct messages, summaries, pins, citations, selected spaces, and binding/cache digests |
| `WIKI-016` | `test_archived_history_is_read_only_and_restore_rejects_pre_archive_turn_fence`; canonical API test | Archived detail/messages/summary/pins readable; new mutation/turn rejected; same conversation accepts a fresh turn after exact restore |
| `WIKI-017` | lifecycle ABA test and `test_higher_role_episode_and_closure_change_rejects_accepted_team_turn` | Archive/restore generation and higher role/space closure changes fail before Agent retrieval and before completion commit |

## 8. Schema and compatibility evidence

- Physical schema head: v11, unchanged at 67 tables, 178 indexes, and 194
  triggers; expected schema SHA-256
  `1638cc6090a60fe1680b67fe6a6394d264b2cd596156bc4062b88948ec75b90a`.
- v11 migration checksum remains
  `d584077e9e774129998b154ddc0b056c2db621ee07ea418185352b1d4b494238`.
- Canonical catalog checksum remains
  `280d1f03ce6e1127d8771cf489c5df393fbcb73bf73bc59498e9fce818c04f82`.
- The schema suite verifies frozen v1-v10 checksums, pristine/current v11
  equality, populated v10-to-v11 preservation, rollback, and no-op replay.
- With the binding flag off, repository SQL does not join the v8 profile table,
  so pre-v8 compatibility and legacy imports retain their prior shape.

## 9. Contract audit

- Report schema: v2; source files: 33.
- Final input SHA-256:
  `aa46f8d3fe5d47a52010c169e240103993a37c64fb533989aabdc101d666e557`.
- Registered totals: 41 requirements, 20 goals, 284 scenarios, 69 OpenAPI
  paths, 75 operations, 31 stable errors, 13 successor flags, and 10
  supersession entries.
- Schema documents/examples: 12/12 valid; 1,612/1,612 references resolved.
- Errors: 0; status: `pass`; exit: 0.

## 10. Residual ownership and handoff

| Residual | Owner/packet | State |
|---|---|---|
| Rich upload, stable references, extraction | `SBE-3.1` | Next prerequisite-ready packet |
| Temporal provenance/footer v12 | `SBE-3.2` | Must follow SBE-3.1; not implemented here |
| LCA aggregate graph and automatic edges | `SBE-4.1`, `SBE-4.2` | Pending |
| Final-ten retrieval/reranker/freshness | `SBE-5.1`, `SBE-5.2` | Pending |
| Multi-Wiki/higher UI | `SBE-6.1`, `SBE-6.2` | Pending |
| Full 1,000-Wiki/40-client/target/company gates and 10111 activation | `SBE-7.1`, `SBE-7.2` | External/later; no claim made |

Next implementation packet: `SBE-3.1`. Read
`second_brain_evolution/EXECUTION_PLAN.md`, `ACCEPTANCE.md`,
`FORWARD_SCHEMA.md`, `evidence/SBE-2.2.md`, `evidence/SBE-2.3.md`, and
`evidence/SBE-3.0.md`. Preserve v1-v11 migration bytes, keep all successor
traffic flags disabled, and do not touch live roots or company providers.
