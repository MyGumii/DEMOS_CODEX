# Current-system inventory and replacement map

This inventory defines what must be migrated, adapted, or retired. It intentionally distinguishes live source systems from derived files and currently orphaned legacy data.

## 1. Application topology

| Component | Current registration | Consequence |
|---|---|---|
| `app_code` | `app_code.py:199-283` registers auth, legacy knowledge, jobs, Notion bridge, Second Brain, memory, chat, sessions | Main Agent and knowledge UI process |
| `app_notion` | `app_notion.py:60-85` registers the same auth/jobs plus full Notion and Outlook | Separate process can publish to Second Brain concurrently |
| Legacy Blueprint | `blueprint.py:43-60` registers only a subset | Composition drift risk; new core must have one shared registration function |
| Path mode | `config.py:29-62` changes roots between standalone/package modes | The same route can currently address different storage roots |

The two legacy standalone processes share `.runtime/users.json` and secret
material but do not share Python locks. `concurrency.py:14-50` protects only one
development process. P7.2 prevents either production web process from owning
local GGUF and makes the historical callable executor/scheduler
development-only; one separately selected worker owns the machine-wide model
mutex.

## 2. Storage and scope inventory

| Area | Current location / behavior | Current fallback | Required disposition |
|---|---|---|---|
| User identity | `.runtime/users.json`; audit in `.runtime/audit.jsonl` | Global | Migrate behind DB-backed user/audit repositories; preserve cookie sessions/password hashes |
| Legacy knowledge | `knowledge/<uid>/*.md`; `knowledge_store.py:28-38` | `None` reads knowledge root | Import owned data; remove root fallback; compatibility resolves authenticated personal space only |
| Second Brain | `knowledge/<uid>/wiki`; `second_brain_store.py:103-145` | `None` becomes `default` | Import into canonical spaces; legacy module becomes read-only adapter |
| SB Markdown | `wiki/sources/*.md`; `_all_docs():316-344` | Per user | Canonical migration input |
| Hidden wiki pages | `wiki/pages/*.md` | Not read by `_all_docs()` | Detect duplicates; import or quarantine with report |
| SB graph | `wiki/graph/graph.json`; `:463-568` | Per user | Import only valid approved edges after item-ID mapping |
| SB candidate state | `wiki/graph/pending_diffs.json`; `:813-821,1575-1663` | Per user | Import as versioned findings where recoverable; otherwise quarantine/report |
| SB index/cache | `index.md`, `wiki_index.sqlite`; `:214-241,351-422,602-675` | Per user | Derived; rebuild, do not import as canonical |
| Backlinks | `graph/backlinks.json` | Per user | Derived; rebuild |
| SB log | `log.md`; `:594-599` | Per user | Preserve as legacy history artifact; new log is an audit projection |
| Sessions | `user_data/<uid>/sessions/*.json`; `routes_sessions.py:32-158` | Unauthenticated uses global `sessions/` | Import to conversation repository; forbid global fallback |
| Pins | Browser `State.pinnedThoughts`; `static/app.js:20` | Tab memory only | Persist as same-space conversation pins |
| Memory | `user_data/<uid>/memory/*.jsonl` | Auth user required | Keep a distinct repository; do not inject private memory into part conversations by default |
| Project context | `user_data/<uid>/projects/<project>/context`; `project_context_store.py:45-62` | Client project ID, default project | Keep distinct from knowledge; bind explicit project context to a conversation and do not auto-inject a user's default project into part scope |
| Notion | `user_data/<uid>/notion/notion.db` and files | Auth user required | Remains Notion source system in phase 1; publication stores immutable snapshot/provenance in knowledge core |
| Notion snapshot support | live `notion.db-wal`, `notion.db-shm`, or `notion.db-journal`; timestamped backup DBs | User Notion directory | Retain live sidecars with the live DB; report backups separately and never silently select one |
| Outlook operational state | `user_data/<uid>/outlook/state.json` | Auth user required | Retain behind the Outlook adapter; do not treat it as knowledge or a durable core job |
| Legacy server logs | root-level `.runtime/*.log` and `user_data/*.log` | Process/service specific | Hash and quarantine/retain as operational evidence; never include contents in an inventory report |
| Notion sharing/public lookup | Scans user-data directories; `notion_store.py:369-526,2119-2147` | Directory-as-registry | Keep behind Notion adapter; do not use it as knowledge authorization |
| Session-page attachment | Notion DB `session_pages`; `notion_store.py:198-204,2821-2903` | User DB | Rebind to canonical conversation ID and explicitly validate accessible page |
| Jobs | `user_data/<uid>/jobs/*.json` plus in-memory closures/executor; `job_manager.py:45-299` | User; admin scans dirs | Replace with durable DB jobs carrying actor and space |
| Local GGUF | Development compatibility may discover/load in process; production `web` is blocked at discovery, resolution, load, and execution, while `worker` requires the machine-wide owner | One Windows GPU server | Durable web producers delegate to the centrally coordinated worker/service; no private model autoload per WSGI process |
| Caches | process dictionaries and per-user strings | Varies | Key by repository namespace + space ID + canonical generation/revision |

`notion_store._connection()` can create directories/database/schema and repair the tree even for reads (`notion_store.py:97-120,140-314`). New adapters must separate deployment migration/bootstrap from ordinary read queries.

## 3. Current canonical/derived reality

Current Second Brain treats Markdown as canonical and then mutates several stores sequentially:

```text
source Markdown -> graph JSON -> wiki SQLite/FTS -> index.md -> log.md
```

An import spans these steps without one transaction (`second_brain_store.py:1128-1212`). `os.replace` prevents a torn individual file but not a cross-process lost update or partial multi-store generation.

Current deletion removes the Markdown, index row, incident edges, and graph node (`second_brain_store.py:1547-1562`). Refresh also prunes endpoints whose files are absent (`:636-651`). File movement therefore cannot implement reversible deactivation.

## 4. Knowledge-related read paths

Every row must be routed through `ScopeResolver` and the new repository or explicitly retained as non-knowledge context.

| Surface | Current route/function | New owner | Backlog phase |
|---|---|---|---|
| Legacy list/view | `routes_knowledge.py:57-68` | Item read compatibility adapter | P3 |
| Legacy search | `routes_knowledge.py:121-136` | Space-scoped retrieval service | P3 |
| SB inbox list/detail | `routes_second_brain.py:224-241` | Canonical finding-table read compatibility in P3; P6 owns generation/review/application | P3/P6 |
| SB item list/detail/system docs | `routes_second_brain.py:355-398` | Item/revision repository; generated system views | P3/P7 |
| SB thought search | `routes_second_brain.py:484-496` | Space-scoped FTS retrieval | P3 |
| Graph read | `routes_second_brain.py:363-384` | Graph repository; read has no writes | P3/P7 |
| Title generation | `routes_second_brain.py:265-279` | Curation helper outside transaction | P6 |
| Session load/list | `routes_sessions.py:191-204` | Conversation repository | P3 |
| Chat status/compact | `routes_chat_stream.py:198-280,796-817` | Conversation service bound to immutable space | P3 |
| Chat legacy KB retrieval | `routes_chat_stream.py:945-957` | Removed/absorbed into one retrieval call | P3 |
| Chat SB retrieval | `routes_chat_stream.py:958-965` | One `space_id` retrieval call | P3 |
| Chat pin retrieval | `routes_chat_stream.py:991-1001` | Same-conversation pin repository | P3 |
| Chat private memory | `routes_chat_stream.py:907-926` | Personal scope only by default; explicit policy for other scopes | P3 |
| Chat project context | `routes_chat_stream.py:940,975-987` | Explicit conversation attachment/selection; no implicit default in part scope | P3 |
| Chat Notion attachment | `routes_chat_stream.py:1008-1020` | Explicit conversation attachment, not implicit knowledge source | P3 |
| Direct memory list/search | `routes_memory.py:17-43` | Retained private-memory repository; authenticated personal context only and never a part-space selector | P3 |
| Project context list/search/scores | `routes_project_context.py:188-197,221-241` | Retained user/project repository; explicit conversation binding remains separate from knowledge scope | P3 |
| Notion bridge page/export/session pages | `routes_notion_bridge.py:172-193,250-252` | Notion source adapter | P4 |
| Notion bridge thought lookup for reverse import | `routes_notion_bridge.py:309-345` (`sb.get_thought()` at `:317`) | Space-scoped item read compatibility adapter feeding the external Notion application | P3 |
| Notion publish preview | `routes_notion.py:1210-1221` | Publication preview service | P4 |
| Notion overview's direct session scan | `routes_notion.py:47-70,1171-1208` | Conversation repository | P3/P4 |
| Jobs list/get | `routes_jobs.py:13-37` | Durable job repository with actor/space capability | P7 |
| Auth users/audit/data summary | `auth.py:598-842` | DB user/audit/admin services | P1 |

Other Notion workspace reads (pages, blocks, comments, versions, files, public links) remain owned by the Notion application in phase 1. They become knowledge-core reads only when a snapshot is explicitly previewed/published.

## 5. Knowledge-related write paths

| Surface | Current route/function | Replacement contract | Phase |
|---|---|---|---|
| Legacy create/upload/delete/reindex | `routes_knowledge.py:70-143` | Publication/lifecycle/projection compatibility commands | P3-P5/P7 |
| SB source import/batch | `routes_second_brain.py:61-201`; async `job_manager.submit` at `:95,191` | Preview + atomic publication service; closure jobs become durable typed inputs | P4/P7 |
| Chat promotion | `routes_second_brain.py:203-222` | Chat source artifact + publication | P4 |
| Curation review/action/batch | `routes_second_brain.py:243-353` | Versioned finding review; no batch destructive action | P6 |
| Thought create/update/delete | `routes_second_brain.py:400-463` | Publication or lease/edit/deactivate commands | P4/P5 |
| Edge create | `routes_second_brain.py:465-482` | Manual/deterministic edge or reviewed semantic proposal | P6 |
| Convert/reindex | `routes_second_brain.py:498-508` | Explicit durable rebuild job | P7 |
| Session save/rename/delete | `routes_sessions.py:180-227` | Conversation transaction with immutable space | P3 |
| Direct memory create/update/delete/purge flag | `routes_memory.py:45-89` (`purge` at `:84`) | Retained private-memory repository and compatibility API; this is not the shared-knowledge lifecycle/purge command | P3 |
| Project context import/delete/clear | `routes_project_context.py:65-219`; async `job_manager.submit` at `:100,161` | Remains user/project data; conversation binding and process-safe repository are separate from shared knowledge; closure jobs become durable typed inputs | P3/P7 |
| Full Notion publish | `routes_notion.py:1223-1247` | Shared publication preview/commit service | P4 |
| Code Notion bridge publish | `routes_notion_bridge.py:198-219` | Same shared service; duplicate logic removed | P4 |
| Notion session attach/detach | `routes_notion.py:1356-1376`, bridge `:224-245` | Canonical conversation attachment relation | P3/P4 |
| Notion save chat summary | `routes_notion_bridge.py:255-307` | Read canonical conversation/messages, then write through the retained Notion adapter; model summarization uses central inference coordination | P3/P7 |
| Notion reverse import of thought | `routes_notion_bridge.py:309-345` | Read a scope-authorized canonical item and create a Notion page; no direct legacy Second Brain read after cutover | P3 |
| Authentication/user CRUD/delete | `auth.py:612-831` | DB user/admin service; disable-first and reference checks | P1 |
| Job cancel | `routes_jobs.py:39-48` | Durable job state/claim/idempotency | P7 |
| Outlook job producers | `routes_outlook.py:103,136,183,197` (`outlook_initial_sync`, `outlook_sync`, `outlook_daily_digest`) | Retained Notion/Outlook handlers registered by durable job type; no Python closure authority | P7 |

`routes_chat_stream.py:916-926` may write user memory on every chat turn, and `:975-987` injects project context by default. Those implicit side effects/reads must be disabled for a part-scoped conversation unless the user explicitly binds that context to the conversation; otherwise a supposedly part-only Agent still carries private personalized state.

## 6. UI integration inventory

| Current UI file | Relevant state/action | Required change |
|---|---|---|
| `static/app.js:20,346,391-400` | Browser-only pinned thoughts, knowledge state reset | Store conversation space and pins server-side; render immutable badge |
| `static/chat.js:52-149,219-242,559` | Session IDs and chat request; sends pins, no space | Create/load scoped conversation; stop client-selected retrieval scope |
| `static/sessions.js:103` | Loads JSON sessions | Load canonical conversation and its space badge |
| `static/knowledge.js:40-1229` | All SB list/import/edit/delete/curation calls | Add scope selector, publication modal, lease editor, inactive/curation states |
| `static/graph.js:179-543` | Graph read, pin, physical delete, edit | Scope graph; persist pins; replace delete/edit with lifecycle/lease APIs |
| `static/skills.js:411-438` | Fetches pinned nodes by current user | Validate same conversation space; no cross-space pin fetch |
| `static/app.js:605-1025` | Existing account-admin UI | Extend with organization tree, one-part assignment, and leader role |
| `notion_static/notion.js:773,826,6977-7024` | One “Second Brain publish” action | Target modal with personal/part/both and snapshot outcomes |

## 7. Legacy behavior that cannot be carried forward

1. Different unauthenticated fallbacks (`knowledge root`, `default/wiki`, global sessions, or error).
2. Standalone vs Blueprint search behavior divergence in `knowledge_store.py:145-169`.
3. `include_legacy` being ignored by `search_thoughts()` (`second_brain_store.py:1565-1572`).
4. `convert_legacy_docs()` being a no-op (`:1890-1896`).
5. `wiki/pages/*.md` being invisible to the normal document scan.
6. Pending diff/candidate functions existing without a consistent reachable workflow.
7. Import-time high-confidence LLM edge auto-write (`second_brain_store.py:979-1017`).
8. Repeated Notion publication using `_unique_name()` to create `-2`, `-3` copies without stable provenance.
9. Browser-only pins disappearing on new session.
10. Session deletion leaving Notion session-page orphan rows.
11. User-data directories acting as a user registry for Notion scans.
12. Admin “transfer” being a workspace file copy rather than canonical repository migration.
13. User deletion recursively removing user/knowledge directories without shared-reference preflight (`auth.py:293-307`).
14. Job inputs existing as process-memory closures and unrecoverable `running` state after restart.
15. GGUF model state and concurrency semaphore living inside the web process, allowing duplicated model loads or uncoordinated inference when process count changes.

## 8. Preliminary migration inventory

Observed on 2026-08-11; P2 must recompute from a frozen snapshot and hashes.

| Scope | Wiki sources | Hidden pages | Other user legacy | Assets | Sessions | Jobs | Notion |
|---|---:|---:|---|---:|---:|---:|---|
| `admin` | 8 | 1 | thought 2, raw 1, inbox 1, raw_chunks 1 | 28 | 5 | 9 JSON | 1 live DB + 1 backup candidate |
| `user01` | 0 | 0 | 0 | 0 | 0 | 2 JSON | 1 live DB |
| Unowned root | 0 | 0 | thought 9, raw 4, inbox 33, edge file 1 | 0 | 0 global | — | — |

User registry contains 2 active users, including 1 system administrator.

Migration source classification:

| Class | Inputs | Action |
|---|---|---|
| Canonical import | user registry, per-user wiki sources, valid graph edges, recoverable candidates, sessions | Idempotent import with owner mapping and hashes |
| External source retained | live Notion DB/files | Keep as Notion SoT; capture immutable knowledge snapshots only on publish |
| Historical preservation | legacy `log.md`, auth audit JSONL | Import as clearly labelled legacy events/artifacts where feasible |
| Rebuild | `index.md`, `wiki_index.sqlite`, backlinks | Do not import; regenerate |
| Explicit adapter/quarantine | root/user legacy raw/thought/inbox/raw_chunks/edges/tombstones | Never guess ownership; report and require mapping |
| Duplicate/orphan decision | `wiki/pages/*.md`, invalid graph endpoints, pending candidates without valid revisions | Report, compare, import or quarantine explicitly |
| Backup-only candidate | Notion backup DBs, job temp files | Checksum/report; never silently prefer over live source |

## 9. Inventory closure gate

Before P2 migration is approved, an automated inventory command must emit a machine-readable manifest containing:

- every scanned root and resolved absolute path;
- every user/owner mapping;
- count, byte size, SHA-256, source class, and planned action for every file/DB record category;
- valid/orphan graph endpoint counts;
- candidate and hidden-page counts;
- asset reference and unreferenced counts;
- session/pin/Notion-attachment linkage counts;
- job state counts;
- exclusions and read errors;
- overall reconciliation totals.

Unknown or unreadable items are report failures, not silently skipped records.

### 9.1 G-02 code-coverage checklist

G-02 inventory coverage is code-based, not satisfied by this document alone. The P0/P2 inventory command must enumerate and classify all of the following:

- route registration in `app_code.py`, `app_notion.py`, and `blueprint.py`;
- every legacy knowledge, Second Brain, curation, graph, index/log, session, memory, project-context, Notion publication/bridge, auth, and job route;
- every chat retrieval or side-effect dependency on legacy knowledge, Second Brain, pins, memory, project context, and Notion session attachments;
- every browser pin/session/knowledge/graph/Notion integration call site;
- every direct import or call of `knowledge_store`, `second_brain_store`, `memory_store`, `project_context_store`, `notion_store`, and `job_manager` from non-test application code;
- every `job_manager.submit` producer, including the two Second Brain producers, two project-context producers, and four Outlook producers listed above.

Expected reproducible discovery commands include:

```powershell
rg -n '@app\.route\(' code_assist_v1 -g 'routes_*.py'
rg -n 'job_manager\.submit\(' code_assist_v1 -g '*.py' -g '!**/tests/**'
rg -n 'knowledge_store|second_brain_store|memory_store|project_context_store|notion_store|job_manager' code_assist_v1 -g '*.py' -g '!**/tests/**'
rg -n 'State\.pinnedThoughts|pinned_thoughts|session_pages|project_context|compact_summary' code_assist_v1 -g '*.js' -g '*.py' -g '!**/tests/**'
```

The inventory artifact must store the code revision/content hash, discovered file and line, symbol or route, read/write/submit/UI classification, scope owner, replacement phase, and disposition. Every discovery match must map to a row in this document or to an explicit retained/excluded record with a reason. The machine-readable coverage report passes only when:

```text
discovered in-scope call sites = mapped call sites + explicitly retained/excluded call sites
unclassified call sites = 0
unexplained direct legacy-store calls after the assigned cutover phase = 0
confirmed job_manager.submit producers = 8
unmapped job_manager.submit producers = 0
```

CI reruns the discovery set and fails on a new unmatched route, store call, or submit producer. Line numbers in this document are review aids; the generated symbol/route mapping and content hash are the durable coverage evidence.

### 9.2 P0.5 executable baseline and current reviewed seal

P0.5 implements the read-only scanner in `tools/legacy_inventory.py`, its fixed
schema in `tools/legacy_inventory_contract.py`, and the code ledger in
`tools/legacy_inventory_code.py` plus
`CURRENT_SYSTEM_INVENTORY_MAP.json`. After the reviewed changes present in the
shared working tree through 2026-08-23, including the retained Notion workspace
page lifecycle and UI-pilot readiness routes plus the canonical browser's
scoped compact-summary binding, the current seal is:

```text
discovered callsites                   693
mapped callsites                       387
retained/excluded callsites            306
unclassified callsites                   0
job_manager.submit producers             8
unmapped submitters                       0
```

Current callsite-ID seal:
`b119b84374c9b74c03a33617f1d54024063c95e49978a49a48e0e1c4b4d4995d`.
The mapping document SHA-256 is
`0a4dbf0d793339bf96ac59b5ff8d88b42293d04cd60fbff09ba88327c64be402`;
the 112-file source fingerprint is
`a03c98223da9279bddc2e4c7ea8889e376c418913f19732d55bd9f554412bf65`.
The Notion route/store/UI sites remain classified by the existing
`retain_external` Notion-source rules, while the canonical browser context is
explicitly mapped to its scoped replacement boundary. This seal update does
not authorize Knowledge Core or part-space writes. The original P0.5 evidence remains
immutable at 667 = 404 + 263 with seal
`e2f971b7d24efb676ca9e5fe5b2f356687998ba28c2148d9164ff39a169a0c39`.

A read-only diagnostic of the current physical layout found zero unknown file
categories and zero source drift. It deliberately rejected `sbtest01`,
`sbtest_recheck01`, and `ggg3g` as registry-unmapped owner claims; P2 must map or
quarantine them rather than infer ownership. `evidence/P0.5.md` contains the
commands, hashes, and regression proof. These observations are not a frozen P2
migration inventory and do not authorize plan/apply.

### 9.3 P2.1 schema-v2 executable closure

P2.1 supersedes the scanner report shape, while preserving the historical P0.5
callsite evidence above. The schema-v2 report fixes 41 categories and three
record kinds (`physical_file`, `logical_record`, `unavailable`), binds all
declared source roles to final local-NTFS/reparse facts, and proves proposal
closure for record IDs, parent/child counts, relationship endpoints, category
aggregates, owner claims, and the overall manifest hash.

The scanner now resolves graph nodes to actual source/page records, validates
candidate source/target evidence, records hidden-page exact-content duplicates,
emits unavailable asset/browser/enumeration records, hashes session core and
collection children, validates legacy job identity/state, and inventories a
read-only scratch copy of Notion SQLite with table/FK/integrity and
session-page-owner resolution. Raw content, passwords, tokens, and unknown
SQLite table names are not emitted.

No frozen production inventory was generated for P2.1. All executable evidence
uses sanitized P0.2 fixtures. The next durable artifact is a P2.2 reviewed plan
bound to a schema-v2 inventory hash and explicit organization mapping.

### 9.4 P7.3 canonical projection authority

The canonical replacement now has an explicit, inert-until-called outbox
projector in `knowledge_core/projection/worker.py`. Space-bound outbox insertion
advances the Markdown projection's desired generation in the same transaction as
the producer's canonical generation. The worker claims with durable owner/
attempt/expiry fencing and generates one complete immutable space generation
through `knowledge_core/projection/materializer.py`; only the small manifest is
atomically replaced after all source/index/log/graph/backlink hashes verify.

The file activity log is intentionally not a raw audit mirror. It includes only
the exact canonical actions tied to visible generation changes, so lease,
conversation, preview, and finding-only audit rows cannot rewrite an existing
generation. Canonical DB/FTS retrieval remains the only Agent authority during
lag or projection error. This implementation does not delete or read from the
legacy file stores, enable compatibility traffic, or start a worker/service.

### 9.5 P7.4 backup and restore authority

`knowledge_core/backup_manifest.py`, `backup_storage.py`, the backup repository,
and the backup/retention/restore services now own the disabled implementation
boundary. A completed backup is one exact directory containing
`database/knowledge-core.sqlite`, `manifest.json`, and only the manifest-bound
CAS blobs. The durable database ledger records the historical run and GC hold;
the presence of a re-verified completed directory determines whether that
historical restore point remains retained.

`tools/backup_restore.py` plus `ops/backup.ps1`, `ops/backup-retention.ps1`, and
`ops/restore-drill.ps1` are explicit operator entry points. Import starts
nothing. Production storage still requires a separately configured off-machine/
off-volume backup root and the P7.5 service account/ACL boundary. All current
execution evidence used disposable P0.2 roots under `D:\CodexTestTemp`; no live
legacy or canonical data was read or modified.

### 9.6 SBE-8.13 agent knowledge-loop UI classification (2026-09-09)

The current code scan contains 749 sites: 393 mapped, 356 retained/excluded,
zero unclassified, and the unchanged eight legacy callable-job producers.
The two additions are conservatively detected dynamic API-helper/probe sites
in `static/agent_integrations.js`. They are explicitly mapped by
`G02-UI-AGENT-KNOWLEDGE-LOOP` to `canonical_agent_integration_bridge` with
disposition `replace`, not excluded as non-knowledge or treated as generic
external data. `agent_integrations/routes.py` and `bridge.py` bind browser
identity/delegated grants and personal Wiki ownership, and use canonical
retrieval, conversation and publication services. The connection/run ledger
is auxiliary; this rule neither expands it to organization Wikis nor waives
the extension's separate acceptance/backup/worker gates.

The new exact callsite-ID seal is
`4ece2ab6d2f4017c438837a0823cb57a4c448ec42d277e8a502a3f1f4280dccf`.
Mapping SHA-256 is
`5931d0f4531f32b0aab3812edafc6a45e077e9badc22690fd562569a9ec8f8aa`.
The scanner itself is unchanged. Its top-level Python/static-JavaScript
discovery is not an exhaustive call graph of the nested integration package;
the bridge/route boundary is inspected and tested separately. A copied real
UI source plus one additional mapped dynamic call still fails the frozen seal.
The seal binds file/symbol/callee/ordinal, not dynamic endpoint meaning: swapping
dynamic targets or adding a relative `api("/new-endpoint")` consumer represented
by the existing helper may not change it. Endpoint-set/const-prefix analysis
would be a separate scanner improvement, not evidence provided by this repair.
Earlier counts/hashes in this document remain historical snapshots.
