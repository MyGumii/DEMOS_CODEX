# UI1 sealed evidence packet result

Recorded: 2026-08-14 23:29 KST  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local infrastructure green; reference observation pending

## Outcome

H1 now has a fail-closed evidence-output boundary in addition to the existing in-memory action, capture, redaction, and manifest validators.

`tools/notion_parity/ui_evidence_packet.py`:

- writes only below an explicitly validated isolated test root;
- derives a unique packet directory from the synthetic run and scenario IDs;
- validates the complete manifest and every supplied artifact before the first write;
- writes deterministic canonical JSON and records the manifest last as the commit marker;
- verifies the exact referenced file set, rejecting missing and extra files;
- rejects duplicate JSON keys, non-canonical JSON bytes, unsafe paths, symlinks and Windows reparse points;
- binds the supplied synthetic-string allowlist to a domain-separated SHA-256 in the manifest;
- recomputes JSON or PNG hashes and checks manifest-to-action, capture, source-build, screenshot, and differential links;
- returns a path-free, identifier-safe aggregate receipt.

The packet does not contain raw DOM, accessibility-tree dumps, browser storage, cookies, network logs, tokens, personal identifiers, or raw screenshots. Redacted screenshots may be admitted only as bounded PNG artifacts explicitly referenced by a redaction-passed manifest. No evidence packet was committed from the local browser run; tests exercise the writer only under pytest's trusted OS temporary root.

## Verification

- evidence packet focused tests: `5 passed in 2.23s`;
- H1 plan/evidence/packet/local-fixture combined tests: `40 passed in 14.80s`;
- isolated real Chrome UI1 golden journey: `2 passed in 30.49s`;
- Python compile for the new production and test modules: pass.

The existing local golden evidence remains unchanged:

| scenario | capture SHA-256 |
|---|---|
| `UI-SHELL-002` | `sha256:e1264295173e4c39994aff5b8e6785e4395ac8b0a5f16ac7c4ec030459a79785` |
| `UI-SEARCH-001` | `sha256:a920c1c018b1ec3d5a9e08296959760ef8605c4632500155dd3a674423c857e0` |
| `UI-EDITOR-001` | `sha256:2a0fda9dcc026a7a2e22ae3563ceb13e36d645de9d55a49a180236982cf7c575` |

Second Brain aggregate remains: cancel item/revision `0/0`, first approval `+1/+1`, unchanged replay `0/0`, and source snapshot provenance exact-match `true`.

## Remaining gate

H1 is not complete. The in-app Browser could not initialize the signed-in Chrome transport and failed before browser selection with `kernel-assets-path-not-found-os-error-3`. A Computer Use fallback was initialized only far enough to reset the shared JavaScript kernel, but it failed at the same kernel-assets write step before listing applications or selecting a window. No Chrome window title, Notion tab, workspace switcher, personal workspace, or personal content was accessed.

After the desktop Browser/Computer Use runtime is restarted or repaired, the next action is C0 read-only confirmation followed by synthetic observation in `REF-FREE-A/B`. Only then can matching reference artifacts be sealed with the same action IDs and the first reference/local differential be produced.
