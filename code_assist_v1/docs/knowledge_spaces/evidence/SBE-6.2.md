# SBE-6.2 evidence

Status: IMPLEMENTED_NOT_GATED

This record covers `SBE-6.2 - higher native write/aggregate graph/reference/
correction UI`. It proves the local implementation and automated gates. It
does not claim a successful in-app Browser run, held-out provider quality, or
production rollout.

## 1. Result

- Implemented and locally checked: 2026-08-25 Asia/Seoul.
- User authorization: v11 authority and v12 temporal changes were approved.
- The exact current Team leader and Responsibility executive receive native
  authoring authority. Inherited and distributed overlay items stay read-only
  and retain source-space, selected-view, origin, grant, and capability data.
- Part graphs expose local edges only. Team and Responsibility graphs compose
  authorized Part clusters and exact-LCA aggregate edges with home, direction,
  status, confidence, model, generation, and evidence provenance.
- Item detail renders an ordered explicit-reference list separately from the
  semantic graph, including unavailable and newer-revision state. The editor
  searches only the server-authorized selected closure and inserts the stable
  `[[item:<uuid>]]` syntax.
- Team/Responsibility distribution targets, active/history grants, create, and
  revoke controls use exact v2 routes. Admin role, write-disable, distribution
  break-glass revoke, and organizational semantic-edge suppression controls use
  explicit confirmations and strict response validation.

## 2. Runtime corrections found by integration

- Higher-native item edits now fence their queued self-curation work with the
  exact mutation authority (organization snapshot, native space, and role
  episode) while the job's expected item/revision state remains separately
  exact. An edit no longer invalidates its own job merely because the content
  closure advanced, and a role/organization ABA still fails closed.
- Graph audit events may bind two items, and aggregate graph events may have a
  home space different from the source item. The sanitized activity projection
  recognizes only `semantic_edge.automatic_activate` and
  `aggregate_edge.create` as graph-level actions and omits their item/revision
  IDs. All ordinary item actions retain the original strict single-item
  integrity rule.
- Distribution history keeps the public maximum page size at 100 while allowing
  one internal look-ahead row for cursor detection. The former `limit=100` 400
  error is covered by the isolated integration gate.

## 3. Verification

```text
Higher authority/routes/publication, aggregate/automatic graph, semantic routes,
scope, browser transport/admin, edit fence, and temporal regression:
151 passed in 164.06s

Final browser transport/ref-picker refinement:
24 passed in 7.45s

v9/v11/v12 migration plus contract-audit regression:
36 passed in 91.49s

Full isolated preview HTTP integration, including restart-idempotent seeding:
1 passed in 15.04s

Direct report-schema-v2 audit before status transition:
status=pass; error_count=0; input_sha256=
1b039dc39ddb578a642c96c75d022103ce1a39746ba07699cbde846294bfa69f;
41 requirements; 20 goals; 284 scenarios; 70 paths; 78 operations; 12 schemas
```

## 4. Acceptance mapping

| IDs | Local result |
|---|---|
| `HSPACE-015..017` | exact native authority, read-only inherited/overlay origin, single native publication target, and stale-role save fence pass |
| `HSPACE-018` | strict admin role/write-disable/break-glass source and DOM contracts pass; real Browser remains blocked |
| `AGRAPH-015..017` | Part aggregate-edge absence, higher cluster/provenance/status rendering, and revalidation correction behavior pass |
| `AUTOEDGE-017..019` | automatic provenance, exact correction capability, suppression/lift re-evaluation, and local failure/retry behavior pass; held-out provider precision remains external |
| `REF-007..008` | ordered available/unavailable/newer list and closure-bound stable-ID picker pass |

## 5. Boundary attestation

- No migration source changed. Frozen SHA-256 values remain v10
  `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c`,
  v11 `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`,
  and v12 `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2`.
- Production successor environment flags remain zero. Only the marker-bound
  preview composition receives the explicit successor flag object.
- No company API, 10011 mutable root, or production service was called or
  changed.
- The in-app Browser runtime again failed before navigation with `failed to
  write kernel assets: path not found`; standalone Playwright was not used as a
  substitute.

## 6. Residual handoff

- Repair the in-app Browser runtime and execute the real keyboard, Korean UI,
  stale-role, and admin confirmation flow.
- Close prerequisite `AUTOEDGE-016` and `RANK-014` with the approved held-out
  provider/evaluation environment. These do not change the local algorithms.
- SBE-7.1 owns the remaining complete regression, cross-process, security,
  fault, scale, and backup/restore matrix.
