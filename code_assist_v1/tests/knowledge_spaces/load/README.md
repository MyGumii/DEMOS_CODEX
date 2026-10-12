# P7.8 target load harness

This directory contains the sealed runner and independent verifiers for the
`PERF-001` target-server gate.  These tools do not provision accounts, start
services, migrate a database, enable feature flags, or authorize traffic.
Running a profile sends real authenticated mutations and therefore requires
an operator-approved target plus the exact confirmation string
`PERF-001_TARGET_LOAD`.

The runner requires both production WSGI endpoints. `--base-url` must resolve
to `CodeAssist-Web`; `--notion-base-url` must resolve to the distinct
`CodeAssist-Notion` listener. Both `/healthz` documents must be ready and bind
the same application version, production configuration, migration catalog,
current migration, SQLite version, and SQLite source ID before authentication.
Web health is sampled once per second during the run, and both services are
rechecked after cooldown.

## Inputs

All input documents are canonical UTF-8 JSON with one trailing LF, no BOM,
duplicate members, CR bytes, or extra fields. Paths must be absolute files on
an existing fixed local NTFS, non-reparse parent. Inputs are identity-bound
while read. Evidence output uses handle-relative create in a pinned parent and
never overwrites an existing file.

The fixture uses schema `code-assist.performance-load-fixture`, version 1. It
contains exactly 40 distinct `client-00` through `client-39` rows, divided into
four part keys with ten clients each, plus one reviewed set of operation
templates and the parser-enforced steady weights/invariant vocabulary. Each
client has:

- one personal and one part space binding;
- one part-shared hot item (the same binding for all ten members);
- one globally distinct write-saturation item;
- one personal canary item/string; and
- exactly 39 leak cases matching every other client's personal canary.

The 1,560 leak cases are each exercised through all five `leak_api`,
`leak_fts`, `leak_agent`, `leak_graph`, and `leak_projection` templates.
Templates may contain only `/api/knowledge/v1/` routes and secret-free
variables. The fixture document SHA-256 and a sanitized `(client_id,user_id)`
binding are copied into every report.

The reviewed templates must include the exact response assertions required by
their profiles. Their secret-free JSON pointers check stale-save revision
stability, dual-publication completeness, durable-job terminal/recoverable
state, and at-most-one domain effect. Every check becomes a separate raw
`invariant` sample; a missing required invariant, one failed assertion, or an
assertion attached to a different fixture step makes that profile fail.

Credentials use schema `code-assist.performance-load-credentials`, version 1,
with exactly the same 40 client/user pairs and a `password`. Keep this document
outside the evidence directory with an operator-only ACL. Passwords are read
only after the traffic confirmation gate and are never copied, hashed,
serialized, logged, or included in exception text.

Hardware uses schema `code-assist.performance-target-hardware`, version 1 and
records `server_id`, Windows version, CPU/logical CPU count, RAM, GPU/VRAM, and
storage model. Its canonical document hash is independently reconstructed by
the report verifier.

## Validate without traffic

`--validate-only` parses every input and checks the two URL shapes without
network access. It deliberately does not require the confirmation string.

```powershell
..\venv\Scripts\python.exe -E tests\knowledge_spaces\load\run_load.py `
  --base-url https://target.example:10011 `
  --notion-base-url https://target.example:10012 `
  --fixture D:\Perf001\fixture.json `
  --credentials D:\Perf001Secrets\credentials.json `
  --hardware D:\Perf001\hardware.json `
  --profile steady --clients 40 --ramp-seconds 120 `
  --duration-seconds 1200 --cooldown-seconds 60 --seed 0x5077 `
  --output D:\Perf001Evidence\steady.json --validate-only
```

## Run the five exact profiles

Use one reviewed fixture, hardware document, seed, target pair, and credential
set for all commands. The profile-specific `(ramp,duration,cooldown)` seconds
are:

| profile | seconds |
|---|---:|
| `steady` | `120,1200,60` |
| `hot-item` | `0,300,60` |
| `write-saturation` | `0,600,60` |
| `projection-burst` | `0,60,60` and exactly 1,000 operations |
| `leak-scan` | `0,0,60` after the complete 40×39×5 matrix |

For each profile, repeat the validation command with its exact values, remove
`--validate-only`, add `--confirm-target-load PERF-001_TARGET_LOAD`, and use a
new `<profile>.json` output. The authoritative JSON is created last and binds
the sibling CSV bytes. Exit 0 means the profile contract and SLO passed, exit 2
means a complete but failing measured profile, and exit 3 means invalid input,
unsafe evidence I/O, or an incomplete run.

## Independent verification and five-profile gate

Verify each report against the exact reviewed fixture:

```powershell
..\venv\Scripts\python.exe -E tests\knowledge_spaces\load\verify_load.py `
  --fixture D:\Perf001\fixture.json `
  --report D:\Perf001Evidence\steady.json `
  --csv D:\Perf001Evidence\steady.csv
```

Then bind all five independently valid reports to one target/build/DB/hardware
identity. The order of `--report-pair` arguments is irrelevant; the artifact
is canonicalized to the frozen profile order.

```powershell
..\venv\Scripts\python.exe -E tests\knowledge_spaces\load\verify_gate.py `
  --fixture D:\Perf001\fixture.json `
  --report-pair D:\Perf001Evidence\steady.json D:\Perf001Evidence\steady.csv `
  --report-pair D:\Perf001Evidence\hot-item.json D:\Perf001Evidence\hot-item.csv `
  --report-pair D:\Perf001Evidence\write-saturation.json D:\Perf001Evidence\write-saturation.csv `
  --report-pair D:\Perf001Evidence\projection-burst.json D:\Perf001Evidence\projection-burst.csv `
  --report-pair D:\Perf001Evidence\leak-scan.json D:\Perf001Evidence\leak-scan.csv `
  --output D:\Perf001Evidence\perf-001-gate.json
```

The gate succeeds only when all five profiles pass and have distinct run IDs
and report hashes while sharing the exact fixture, 40-client binding, seed,
hardware document, Web/Notion target origins, application/config/migration,
and SQLite identities. A local workstation/unit run is harness evidence only;
it cannot satisfy `PERF-001` or close P7.8.
