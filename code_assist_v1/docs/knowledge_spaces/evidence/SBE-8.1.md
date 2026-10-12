# SBE-8.1 gold organization and UI repair evidence

> Superseded for integrated selector, initial-workspace, and generic inventory
> query claims by `SBE-8.2.md`. The corpus/authority/graph fixture results in
> this file remain valid, but the earlier UI completion statement was too broad:
> its browser mock did not contain the complete non-empty finding shape returned
> by the retained 10211 server, and its Codex gate used exact fixture keywords
> rather than a generic knowledge-inventory question.

Date: 2026-08-25 Asia/Seoul

Status: LOCAL_GOLD_PASS_BROWSER_RUNTIME_BLOCKED

## 1. Release candidate

- Code root: `G:\AI_Agent\DEMOS_CODEX\code_assist_v1`
- Isolated data root: `D:\CodexTestTemp\SBE8-Gold-20260825-v1`
- Inspection URL: `http://127.0.0.1:10211`
- Listener: PID 43364, launcher PID 1424
- Marker SHA-256:
  `83c35d85ab9cdaba4359c33d9ccb2fa915c22d134220f99eb7a7327e70200ee1`
- Health: `ready`, schema version 2, `company_api_called=false`

The source organization was opened through SQLite `mode=ro` from
`D:\CodexTestTemp\SecondBrainFullPreview-20260817-v2\core\knowledge_core.db`.
The exact source SHA-256 remained
`a6b297848df8465442b7c62193b6333fbc8798a16a2b065baa791193a29dcd7e`
and its size remained 9,584,640 bytes. No source credential, password, or token
was read or copied.

## 2. UI and selector repairs

- An exact non-default personal Wiki is now requested from the server through
  `selected_space_id` and is appended to the four base scope options only after
  server-side authorization.
- Existing conversations resolve their immutable Wiki through
  `conversation_id`; browser/local selection state is not authority.
- Finding pages accept and validate the current
  `corpus_closure_sha256` contract, removing the stale exact-shape rejection.
- The left tree renders only personal Wiki directories. Part, Team, and
  Responsibility remain in the top scope selector and are not duplicated as
  tree directories.
- Personal Wiki rows support drag/drop and keyboard Up/Down ordering. Order is
  persisted per signed-in user in browser-local state and does not alter Wiki
  authority, identity, or conversation binding.
- Static asset cache keys were advanced to the SBE-8 repair revision.

The older preview could open Part because it used the old finding response
shape and only the four base scope options. The failing preview combined the
new closure hash and exact multi-Wiki binding with the old browser response
shape and a four-option synthesized selector. The server and browser halves are
now one contract.

## 3. Gold organization and corpus

The read-only source organization was copied into the isolated canonical DB:

- 1 Responsibility: `AMHS담당`
- 3 Teams: `AMHS지능화`, `이천AMHS`, `청주AMHS`
- 7 Parts, preserving their source IDs and names
- 70 `sbv-*` members, exactly 10 per Part
- 7 Part leaders copied from the source
- 3 independent Team leaders and 1 independent Responsibility executive

Gold documents created in the isolated root:

| Scope | Wiki/page count |
|---|---:|
| Personal Wikis | 140 (2 per member) |
| Personal pages | 420 |
| Part pages | 77 |
| Team-native pages | 6 |
| Responsibility-native pages | 2 |
| Gold pages total | 505 |

The corpus mixes exact related pairs and deliberately unrelated pages. The
later HTTP gate also committed 7 independent Personal+Part publications, 3
Team-leader native publications, and 1 Responsibility-executive native
publication through the real preview/commit authority path.

## 4. Functional gate

`gold_functional_gate.status=pass` proved:

- 70 copied accounts are resolvable; a copied account can log in with the
  isolated preview password.
- 140/140 personal Wiki selectors, document lists, graphs, logs, index status,
  searches, and exact conversation bindings pass.
- 7/7 Part views and 3/3 Team views pass; the Responsibility view passes.
- 11 organization conversations preserve exact scope binding: 7 Part, 3 Team,
  and 1 Responsibility.
- 11 peer-sharing searches pass across Part, Team, and Responsibility scopes.
- 7 Personal+Part commits create distinct item IDs in distinct spaces.
- 3 Team leaders and 1 Responsibility executive publish natively through their
  v11 role-assignment generation fence.

## 5. Graph contract audit

`graph_contract_audit.status=pass` proved:

- 140 personal graphs have at least one local automatic edge.
- 7 Part graphs have at least nine local automatic edges.
- local edges crossing their selected space: 0.
- aggregate automatic edges: Team 4, Responsibility 2.
- aggregate edges under Personal or Part home spaces: 0.
- deliberately unrelated fixtures: 151; automatic-edge touches: 0.

This preserves Part-local graph isolation. Cross-Part and cross-Team relations
exist only in Team/Responsibility aggregate graphs.

## 6. Codex-only validation

Both Codex gates passed with provider order fixed to `codex` and model
`codex-local`:

- bridge health marker: pass;
- canonical grounding queries: 4;
- inherited Team/Responsibility grounding queries: 2;
- company API calls: 0.

The grounding gate retrieved exact Personal and Part hits, a Part hit inherited
through Team, and a Team hit inherited through Responsibility, then passed only
those canonical snippets to Codex. Codex returned all expected answer keys.
Hynix/company endpoints were not contacted.

## 7. Regression evidence

- UI, transport, scope, full-preview, v10/v11/v12, temporal, and higher
  publication tests: 63 passed.
- contract audit, composition, P6 acceptance, and schema acceptance tests:
  102 passed.
- total selected regression tests: 165 passed.
- Python compilation and JavaScript source/DOM contract tests passed.
- Frozen migration SHA-256 values remain:
  - v10: `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c`
  - v11: `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`
  - v12: `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2`

## 8. Remaining external proof

The in-app Browser runtime failed before navigation with
`failed to write kernel assets: path not found`. This is a Browser runtime
bootstrap failure, not a 10211 HTTP failure; `/healthz` and the root route both
return HTTP 200. Pixel-level login/click/drag screenshots remain a target-runner
handoff item. Standalone Playwright or Computer Use was not substituted for the
required Browser surface.

Port 10111 remains owned by the independent approved-dev-pair process
`AP-DEV-PAIR`; port 10121 remains the earlier SBE-7 preview. Neither process was
stopped or modified.
