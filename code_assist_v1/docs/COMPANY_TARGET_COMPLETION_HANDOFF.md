# Company target completion handoff

Status: P0-P6 and P7.1-P7.7 are verified; the complete P2 frozen-copy migration
gate is verified. The canonical Agent runtime has also passed an isolated real
Codex end-to-end functional gate. P7.8, P7.9, the company-provider wire gate,
and P8 require the company target and remain open.

This packet is executable by the company-side Codex. It does not contain or
authorize export of passwords, provider tokens, private prompts, source bodies,
service-account secrets, TLS keys, or production database bytes. A company
operator must supply those through the approved secret and deployment paths.

## 1. Exact prerequisite evidence

The authoritative migration evidence is
`docs/knowledge_spaces/evidence/P2.8.md`. The frozen artifacts are outside the
source copy under `G:\AI_Agent\CodeAssist-P2.8-20260817-v3\migration` on the
personal machine. Transfer only through the approved company path and verify:

```text
snapshot attestation document  1e43469219efb3b88c23daff631a4f52e9fbb87b22531b9345cd990a5ee2cb0f
inventory document             172235a2830d12c36e1de35ccae0350200081ace746b71f013eb224920f5b51e
organization map               67ba3cdc4fdbf9cd955330d3146f5b986b9d5efa90b1f8b7c3a4c9df5fdc206c
plan draft                     8b72f9902bc53d94487bdf12c0086957f3de27ccfff13b10f061ec776b86e0fd
review attestation             d2822388469d57e82fc3fdbba6354476cafb5d01b309d9ac6b19dfc4b15d3885
reviewed plan document         d30c08d06f56dc80961cd6844d0d0ab662bae4c8827f3fc40e93af46700550da
reviewed internal plan         b23d62bb8038dcd92ecd71b48adfe3baad8b1d0837093b8c157d558d4599f414
rehearsal document             cf91383e9703a8082eb9d991989d0df357ea0aeaaa2f2a3b4721764c4e27230b
strict verification            2fc9798b8e99d11fb9383015cd08d56283fb0ad1e246f7d2375989328b33f7de
```

Do not reuse the personal target databases for production. Recreate target
state from the reviewed artifacts and current deployment package.

## 2. Why the remaining gates cannot run here

The personal host is Windows 10 build 19045, six logical CPUs, about 48 GiB
RAM, and a GTX 1050 with 2 GiB VRAM. It has no approved 40-account/four-part
fixture, production TLS listeners, company service account, external backup
destination, or company provider token. It is not the documented target-class
Windows server.

The local backup hardening also behaved fail-closed: the approved C: source
could not use G: as the distinct-volume backup root because the installed host
security module rejected the required pinned directory handle with `WinError
5`. Using C: for both source and backup would violate the off-volume contract,
so no bypass was attempted. The source attempt is isolated and is not P7.9
evidence.

## 3. P7.8 forty-client target gate

Use `tests/knowledge_spaces/load/README.md` without shortening or rescaling the
profiles. Required inputs are one canonical 40-account/four-part fixture, a
separate operator-only credentials file, a canonical hardware document, two
distinct production WSGI HTTPS origins, the fixed seed, and explicit
`PERF-001_TARGET_LOAD` confirmation.

Before traffic:

1. verify the deployed build/config/catalog/schema/SQLite source ID on both
   `/healthz` endpoints;
2. verify the database/blob/projection/evidence roots are fixed local NTFS and
   non-reparse;
3. run all five commands once with `--validate-only`;
4. confirm the 40 client/user pairs and 1,560 directed cross-space canaries;
5. archive the canonical fixture and hardware document hashes, never the
   credential contents.

Then run exactly:

| Profile | Ramp | Duration | Cooldown |
|---|---:|---:|---:|
| steady | 120 s | 1,200 s | 60 s |
| hot-item | 0 s | 300 s | 60 s |
| write-saturation | 0 s | 600 s | 60 s |
| projection-burst | 0 s | 60 s | 60 s |
| leak-scan | 0 s | complete 40x39x5 matrix | 60 s |

Each real command must add `--confirm-target-load PERF-001_TARGET_LOAD` and
write a new create-only JSON/CSV pair. Verify every pair with
`tests/knowledge_spaces/load/verify_load.py`, then bind all five with
`verify_gate.py`. P7.8 passes only if the gate artifact passes, leak/stale-save/
partial-dual/lost-job/duplicate-effect counts are zero, and every documented
latency/busy/projection threshold passes. Archive raw samples; do not report
only percentiles.

## 4. P7.9 signed target restore

Run against the same reviewed build/config/catalog/SQLite source ID as P7.8.
The backup destination must be on an approved different machine or volume with
the reviewed ACL/encryption policy. Do not enable a test-mode same-volume
exception.

1. Use `code_assist_v1.tools.backup_restore create` against the drained or
   online canonical source with a fresh UUID run ID.
2. Verify the completed self-hashed manifest, SQLite snapshot, exact referenced
   blob set, GC-hold closure, `quick_check`, and foreign keys.
3. Restore that run with `restore-drill` into a distinct empty data root and
   isolated ports.
4. Rebuild every projection and run canary authentication, personal/part read,
   edit, dual-publication response-loss retry, search, graph, and projection
   checks.
5. Record incident time, backup audit high-water, drill start/end, RPO, RTO,
   integrity/FK/projection mismatches, application/build/config/catalog/schema,
   SQLite version/source ID, target hardware, and all artifact hashes.
6. Require RPO at most 60 minutes, RTO at most 240 minutes, and every mismatch
   count zero.
7. Have the authorized company reviewer sign the canonical report hash through
   the approved signing/attestation system. Store only reviewer ID, UTC,
   signature/key identifier, decision, and document SHA in the repository; keep
   private key material external.

P7 closes only when both P7.8 and P7.9 are independently verified and the
production service/SQLite/backup observations match their artifacts.

## 5. Company provider gate

Execute `COMPANY_OPENAI_COMPATIBLE_HANDOFF.md`. The company endpoint contract is
OpenAI-compatible `POST /v1/chat/completions`; exact deployed IDs for Gemma-4,
Qwen3.6, Kimi-K3, and GLM-5.2 must come from the company registry. The automatic
lane order is exactly Gemma-4, Qwen3.6, Kimi-K3, GLM-5.2, local GGUF, then
Codex only when `--codex` was present. The reviewed external registry must
carry that exact `default_model_priority` array and retain the approved GGUF
profile; `codex-local` must not be placed in the array. Do not guess missing
model IDs or export a token.

Before service startup, run the create-only
`code_assist_v1.tools.company_provider_registry_audit` command documented in
the provider handoff and require exit `0`. Bind its report hash and the exact
external registry hash into both health/approval packets; the audit performs
no network or secret read and therefore precedes the live model probe.
After the live probe, run its `--verify-report` mode against the exact probe
config and require exit `0` before accepting the provider packet.

Set `CODE_ASSIST_API_CONFIG` in the protected production environment to that
reviewed repository-external registry. Production uses integrated mode and
otherwise loads the unrelated repository-root registry. Both WSGI health
checks and the signed approval packet must bind the exact selected config hash.

The personal Codex bridge is separately proven live with `codex-cli 0.147.0`,
ChatGPT login, read-only ephemeral execution, and exact streamed result
`BRIDGE_OK:EVIDENCE-CODEX-BRIDGE-20260817`. That does not substitute for the
company endpoint probe.

The bridge has additionally been exercised through the real canonical Agent
stack rather than only as a standalone marker. A fresh isolated database was
seeded with distinct Part A, Part B, and personal canaries; the public login,
space, scoped-search, conversation, SSE-turn, and readback routes were used;
the real Codex answer was exactly `ORBIT-COPPER-7319`; and the foreign and
personal canaries were absent. The isolated registry had one Codex entry and no
API token or API entry, so `company_api_called=false` is structurally bound.
Evidence and source hashes are in
`docs/knowledge_spaces/evidence/LOCAL_SECOND_BRAIN_CODEX_VALIDATION.md`.

This narrows the company remainder to the actual four-model registry and wire
contract plus target operations. The company-side Codex must still prove the
exact deployed IDs, authentication, streaming/error behavior, latency, and
fallback order against the protected OpenAI-compatible endpoint. It must also
run the 40-client and signed off-volume restore gates; the single-user local
server is not a substitute for either.

## 6. P8 advancement

Do not begin P8 until P7.8/P7.9 pass. Then follow P8.1-P8.7 in
`IMPLEMENTATION_BACKLOG.md` and the no-go list in `TEST_OPERATIONS.md`:

1. assemble the approval packet with P2, P7.8, P7.9, company-provider, backup,
   rollback, org-map, and configuration hashes;
2. take a fresh final inventory/plan or prove the reviewed source binding is
   still exact;
3. perform maintenance cutover with personal reads/writes first and part writes
   off;
4. run personal canary/soak, then one-part read pilot, then explicitly approved
   part writes;
5. stop and roll forward/recover on any no-go condition; do not fabricate a
   rollback to legacy after the first canonical write;
6. broaden to 40 users only after the pilot evidence passes;
7. run the final `G-01..G-14`, `REG-001..003`, schema, docs, migration,
   performance, restore, and provider audit and archive the signed decision.

The company-side Codex should return the P7.8 gate JSON, P7.9 signed report,
provider packet, P8 approval/cutover artifacts, sanitized test logs, exact
hashes, and a list of any code changes. Any missing external authority or
secret must remain an explicit blocker rather than an inferred approval.

## 7. v13 organization homes and worker-count boundary

The current forward schema head is v13.  Every active user has exactly one
direct organization home:

- an ordinary member or Part leader is directly affiliated with one Part;
- a Team leader is directly affiliated with that Team and has no synthetic
  descendant-Part membership;
- a Responsibility executive is directly affiliated with that Responsibility
  and has no synthetic Team/Part membership.

The exact open higher-role episode is still required for native Team or
Responsibility writes.  Target fixtures, account imports, load identities, and
leak canaries must preserve these direct homes instead of assigning the Team
leader or executive to an arbitrary descendant Part.

Keep the preview worker topology at one durable worker and one projector per
canonical root unless a separate target experiment proves a larger count.  The
inference runtime deliberately enforces one machine owner; starting another
durable inference worker is expected to fail with `InferenceOwnerBusyError`,
not increase model throughput.  Local dense-burst experiments with multiple
projectors also produced retry contention around SQLite generations and
manifest replacement.  Claim ordering now prefers independent spaces and a
replayed event can acknowledge an already-settled current generation, but that
hardening is not target-scale multi-projector evidence.

P7.8 must therefore measure the supported one-worker/one-projector topology.
If the company needs more projector processes, run a separately identified
gate and require zero dead events, zero duplicate effects, zero generation
regression, zero manifest checksum mismatch, and convergence after forced
claim expiry.  Do not treat process count alone as a performance fix.

## 8. SBE-8.8 functional baseline to preserve

The company target run must preserve the locally verified v13 behavior in
`docs/knowledge_spaces/evidence/SBE-8.8.md`. The isolated functional matrix
used 19 accounts, ten real files per account, 220 independent publications,
57 current projection checkpoints, seven isolated Part graphs, three Team
aggregates, one Responsibility aggregate, and 62 completed Agent queries. Its
create-only report has SHA-256
`D7BEC47BB808708DB39FBC2641F18ECD1519DBCA2033D9C98DB9CD5AAA03A6CD`.

That matrix is a correctness baseline, not P7.8 evidence. Re-run the canonical
40-client profiles without reducing duration or concurrency, using direct
Part/Team/Responsibility homes and the supported one-durable/one-projector
topology. Require zero nonterminal/dead jobs, zero unapplied outbox events,
current manifest/graph hashes, and the existing leak/stale-save/latency gates.
Run the real Browser drag/drop/multi-select journey separately because the
local in-app Browser transport was unavailable during the final SBE-8.8 run.
