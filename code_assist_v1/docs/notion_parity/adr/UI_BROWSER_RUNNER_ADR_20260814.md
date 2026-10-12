# ADR: Core Free UI browser runner

Status: accepted for isolated local runs; signed-in Chrome reference remains separate  
Recorded: 2026-08-14 (Asia/Seoul)  
Scope: `UI1-FREE-DESKTOP-GOLDEN-JOURNEY`, H1 Packet D–F

## Decision

Keep the signed-in Chrome Browser control surface as the only reference runner.
Adopt Python Playwright `1.62.0` from
`requirements-ui-browser.txt` for the isolated local target only.  Launch the
installed Stable Chrome channel with a non-persistent browser context under the
trusted OS test root; never point Playwright at the user's Chrome profile.

The first connection attempt and one clean-session retry both failed before any
browser was selected with the host error `failed to write kernel assets: path
not found`. The browser plugin entrypoint existed and its SHA-256 was verified.
No tab was opened, no cookie/profile state was inspected, and no Notion or local
page was accessed. This is a host automation failure, not product evidence.

The local fallback has since executed Stable Chrome successfully without a
browser download or persistent profile. The isolated shell/search/editor and
canonical Second Brain cancel/approve/replay journeys each reproduce exactly
across two fresh roots. All six UI1 local capture envelopes are present, and
the keyboard/focus journey reaches its five required controls and returns to
Second Brain. This does not authorize the fallback for the signed-in Notion
reference and does not turn local evidence into a reference or differential
pass.

## Required capabilities

The adopted runner must support, on Windows and Chrome desktop:

- deterministic semantic action ordering;
- Korean IME input;
- keyboard, pointer, clipboard, focus, and Escape recovery;
- accessible role/name/state capture;
- viewport and bounding-rectangle measurement;
- screenshots that can be redacted before repository evidence is written;
- two isolated local reruns without a persistent user profile;
- explicit separation between synthetic local state and the signed-in Notion
  reference session;
- zero cookie, local-storage, raw DOM, raw network, token, or private identifier
  export.

## Options considered

| Option | Local app | Signed-in reference | Determinism | Repository impact | Decision |
|---|---:|---:|---:|---:|---|
| Existing Chrome Browser control | yes | yes | to be proven | none | primary; retry |
| Test-only Playwright runner | yes | forbidden | strong | one pinned test requirements file; installed Stable Chrome required | adopted for local only |
| Selenium/WebDriver | yes | unsafe if pointed at a personal profile | adequate | driver/version management and CI policy | rejected for now |
| Puppeteer | yes | no shared signed-in profile by default | strong for Chromium | Node dependency and browser/cache policy | not preferred |
| Manual screenshots | partial | yes | not repeatable | no code dependency | characterization aid only; never a passing runner |

## Adopted Playwright boundary

The adopted dependency contract is:

1. package: `playwright==1.62.0`, Apache-2.0;
2. interpreter at adoption: repository venv Python `3.14.3`;
3. browser: installed Stable Chrome through Playwright's `channel="chrome"`;
4. browser download: none; `playwright install` is not part of this contract;
5. install: `venv\Scripts\python.exe -m pip install -r code_assist_v1\requirements-ui-browser.txt`;
6. removal/rollback: remove the requirements file and browser tests, then
   recreate the repository venv from its approved baseline rather than
   uninstalling shared transitive packages in place;
7. CI/offline: the browser tests skip when Playwright is absent; a release gate
   counts H1-L4 only on a Windows worker with Playwright and Stable Chrome
   already installed, and a skip is never a pass;
8. artifacts: screenshots and structured captures remain outside the
   repository until redaction verification succeeds;
9. credentials: the runner receives only a runtime-generated local password,
   uses no saved profile, and cannot be used against Notion reference URLs.

## Adopted local fixture boundary

`code_assist_v1.tools.notion_parity.ui_local_fixture` is the only launcher
adopted by this ADR. It:

- requires `--test-mode-confirmed`;
- rejects repository, package, and live-data roots;
- binds only `127.0.0.1`;
- maps every mutable application path to one trusted OS temporary root;
- removes API, Codex, Microsoft, and document-converter credentials;
- disables model autoload, bridges, visible Office automation, and signup after
  the first synthetic account;
- validates the effective runtime paths in a child process before browser use;
- emits only a path-free fixture descriptor as durable evidence.

Passing its health check proves process isolation, not browser fidelity.  The
real-browser test additionally proves local rendering and interaction, but not
reference parity.

## Resume command shape

The next browser run must use a fresh OS temporary directory and a free loopback
port, then execute:

```text
python -m code_assist_v1.tools.notion_parity.ui_local_fixture check
python -m code_assist_v1.tools.notion_parity.ui_local_fixture serve
```

Both commands also require the explicit data-root, repository-root, port, and
`--test-mode-confirmed` arguments. Exact absolute paths belong only in the
ephemeral run record, never in checked-in UI evidence.

## Consequences

- H1 Packet A–C remains valid.
- Packet D is a real-browser repeatability pass for all six local UI1 scenarios
  on the current snapshot. The earlier
  `UI1-BLOCKER-SB-LEGACY-NONIDEMPOTENT` observation remains historical: the
  adopted isolated fixture now exercises the canonical publication boundary,
  where preview cancel writes zero, the first approval creates one item and one
  immutable revision, and unchanged replay creates zero additional records.
- The local-only publication modal now has dialog semantics, receives focus on
  preview/result open, and restores focus to its origin when closed. This is a
  Second Brain hard-gate correction, not a Notion reference comparison.
- Packet E cannot begin until C0 confirms only the two synthetic Free
  workspaces without opening personal content.
- Business, Enterprise, Archive, payment, invite, public posting, production
  migration, live flags, continuous consumers, and resolver cutover remain out
  of scope.
