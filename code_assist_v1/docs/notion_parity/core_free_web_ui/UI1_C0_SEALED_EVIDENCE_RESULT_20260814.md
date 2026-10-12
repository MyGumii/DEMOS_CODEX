# UI1 C0-bound sealed evidence result

Recorded: 2026-08-14 23:49 KST  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local H1 infrastructure green; live reference pending

## Outcome

The identifier-free C0 decision is now part of the evidence packet's cryptographic provenance rather than a detached checklist result.

For every packet containing a reference capture, `tools/notion_parity/ui_evidence_packet.py` now requires:

- an exact `ReferenceC0Decision` whose status and derived authorization are internally consistent;
- a passing decision with the same synthetic `run_id` as the evidence manifest;
- a canonical `packet-seal.json` that binds the manifest SHA-256, an ordered artifact-index SHA-256, the complete identifier-free C0 descriptor, and the C0 SHA-256;
- the seal and manifest as reserved names that ordinary artifacts cannot claim;
- canonical seal bytes and exact seal fields during verification.

A reference packet without C0, with a failed or cross-run C0, with a modified C0 hash, or with a modified manifest/artifact index fails before promotion. Packets without reference evidence reject a stray C0 decision. The packet receipt exposes only canonical hashes, synthetic IDs, counts, and `path_disclosed=false`.

## Verification

- C0 guard plus packet focused tests: `32 passed in 7.83s`;
- complete H1 guard/evidence/packet/plan/local-fixture selection: `67 passed in 19.39s`;
- production and test modules compile: pass;
- exact Core plan verifier remains green with 25 required scenarios and 6 UI1 scenarios.

The prior isolated real Chrome golden journey remains the current local browser proof: `2 passed in 30.49s`, with Second Brain cancel `0/0`, first approve `+1/+1`, unchanged replay `0/0`, and source snapshot match `true`.

## Safety and remaining gate

No raw or personal Notion identifier is accepted by the C0 or packet APIs. No raw evidence was written outside pytest's trusted OS temporary root. Protected application, auth, Knowledge Core, Second Brain, live data, and `AGENTS.md` files remain unchanged.

The Browser/Computer Use shared JavaScript kernel still fails before application listing with `kernel-assets-path-not-found-os-error-3`. Therefore this result proves the reference safety and provenance machinery but not a live Notion observation. After runtime recovery, a real C0 pass in the signed-in Chrome session is required before entering `REF-FREE-A/B` or sealing reference evidence.
