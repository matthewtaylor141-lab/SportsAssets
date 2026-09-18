# Bounded monitoring release — COMMAND, 2026-09-18

Authorised by management the same day: *"Prepare and release the smallest
version needed for COMMAND sign-in, authenticated real-data monitoring, and
durable budget display."*

## What this release is

A **monitoring surface**, not a read-only package. The monitoring endpoints
are read-only; **the migration changes the database**, and the deploy
restarts both Render services. Those two facts are stated here rather than
folded into the word "read-only", because they are the parts that carry
operational risk.

## Scope

**Ships enabled**

| Route | Method | What it does |
|---|---|---|
| `/api/command/session` | POST / DELETE | Sign in / out. Sets an HttpOnly, Secure, SameSite=Lax cookie scoped to `Path=/api/command`. The token is never in a response body. |
| `/api/command/snapshot` | GET | `bt.command.v1` built from the real ledger. |
| `/api/command/investor/snapshot` | GET | The investor projection, made server-side. |
| `/api/calibration/state` | GET | The durable budget: spent / reserved / remaining. |
| `/api/calibration/preflight` | POST | Read-only pre-submission report for one ticket. |
| `/api/calibration/stop` | POST | **The operator stop.** Deliberately enabled — it removes authority rather than granting it, and a monitoring surface whose stop does not work is missing the one control an operator may need in a hurry. |

**Ships disabled** — gated by the single constant
`api/app.py :: CALIBRATION_WRITES_ENABLED = False`, read by the dependency
`require_calibration_writes`, which answers **503** with a named reason.

| Route | Why it is off |
|---|---|
| `/api/calibration/approve` | Takes a reserve and records an approved ticket: money accounting. |
| `/api/calibration/release` | Frees a reserve. |
| `/api/calibration/resume` | Lifts an operator stop. |

**Venue submission**: none exists. `calibration_execute.py` is built and
tested offline and is imported by **no** module under `sportsassets/api/`;
a test asserts that absence, so "disabled" is a property of the code rather
than a promise.

## Migration

`065_calibration_lifecycles.sql` — **purely additive**: two new tables, two
new indexes. It alters no existing table, drops no column, rewrites no row.
Applied at API boot, as every migration here is.

**Compatibility**: an older API build runs unchanged against a database that
has these tables; they are simply unused. So the preferred rollback is to
revert the code and *leave the tables in place*.

**Rollback**: `migrations/rollback/065_calibration_lifecycles.down.sql`. It is
destructive — it drops the calibration ledger, which is the audit trail of a
money experiment — so it is not part of an ordinary rollback and the file
says so, with the export commands and the open-lifecycle check to run first.

**No ledger reset**: nothing in this release deletes, zeroes or rewrites an
existing row in any table. Budget history starts at this release and only
accumulates; `spent_usd` is increment-only with a `CHECK (spent_usd >= 0)`.

## Trading

Not changed and not activated.

- `git diff --stat c762b4b..HEAD` (the last untagged, i.e. last-deployed,
  commit) over `backend/`, `edge-engine/`, `rn1-collector/`, `pmus-broker/`,
  `ops/`, `render.yaml` touches **only** `api/app.py`, two new modules, one
  migration and tests. **No worker file, no `live_executor.py`, no `pmus.py`,
  no `mirror_live*`, no `render.yaml`.**
- `mirror_live=false` is a row in `ingestion_state`. A deploy restarts the
  worker; it does not change that row, and the worker arms only when it reads
  exactly `true`.
- **The deploy does restart the worker process.** That is an operational
  event and is named here rather than described as "untouched".

## Verification still owed

Nothing in this document is a claim that the deployed build works. The
deployed browser check — actual records reconciled to backend ids,
quantities, statuses and source timestamps — has not been done, and the
snapshot's own gate row `Deployed browser reconciliation` reads `PENDING`
until it has.
