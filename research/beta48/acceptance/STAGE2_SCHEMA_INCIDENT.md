# The phantom column — and a correction to how I described all three failures

2026-09-22. Production, `fd39a0a9`, probe `f99cc7d9-535d-41ee-9780-7b2d6f67035a`.

## What happened

Armed 08:05:40Z with a fresh identity, an open allowance and a 1,800 s deadline.
The loop woke on its new 30 s poll and, at **08:06:36.604Z**:

```
listing reservation refused for - (RESERVATION_UNREADABLE: reservation
failed: UndefinedColumnError; treated as SPENT and nothing is dispatched)
not starting (DISCOVERY_FAILED); holding 300s (acquisition backoff)
```

`ctl.reserve()` wrote `SET value = jsonb_set(...), updated_at = now()`.
`backend/migrations/001_init.sql` declares:

```sql
CREATE TABLE IF NOT EXISTS ingestion_state (
    key    TEXT PRIMARY KEY,
    value  JSONB NOT NULL
);
```

Two columns. No `updated_at`, and there never has been one —
confirmed across **all 91 migrations**: `001_init.sql` is the only file that
names `ingestion_state`, and there is no later `ALTER TABLE` on it. (`updated_at`
*does* appear in 001 on three other tables, at lines 39, 100 and 145, which is
how the mistake was plausible.)

**No production schema alteration is required.** The schema is correct. The fix
is to stop writing a column that does not exist, which is what `4b2c7bdd` does.

It failed closed: zero venue requests, all three counters still 0, no orders,
no observation rows. The row is preserved verbatim in
`incidents/probe-f99cc7d9.json`.

## The correction

The commit message for `4b2c7bdd` says:

> Three times now the gap has been the environment, not the logic.

**That is wrong, and it obscures the cause we need to eliminate.** The accurate
statement is:

> **Our code and fixtures did not match the production interfaces.**

Nothing about the production environment was unusual, misconfigured, degraded or
unavailable. Production behaved exactly as its schema and its call signatures
specify. Every one of the three failures was on our side: the code assumed an
interface production does not offer, and the fixtures were written from the same
assumption — so the fixtures could never contradict the code.

"An environment issue" points outward, at something to be tolerated or worked
around. The real cause points inward, at something to be fixed, and it has a
rule attached.

## The same cause, three times

| | The production interface | What our code assumed | What our fixtures supplied | Cost |
|---|---|---|---|---|
| 1 | `workers/all.py` calls `bettor_live_loop.main()` **with no arguments** | a `control_pool` would be passed in | a pool, in every test | probe `12724d1e` — `NO_CONTROL_POOL` |
| 2 | `main()` with no arguments passes **`client=None`** into `probe()` | a client with `.markets.bbo` | a client, in every test | would have burned all 40 distinct slots on reads that never left the process; caught before it ran |
| 3 | `ingestion_state` has columns **`(key, value)`** | an `updated_at` column as well | a `CREATE TABLE` the fixtures wrote themselves, with `updated_at` | probe `f99cc7d9` — `UndefinedColumnError` |

In every row, the local proof passed **because** it supplied the very seam it
was meant to test. That is the pattern, and it is not an environment pattern.

## The rule that follows

**A proof is worth nothing if it supplies the seam it is meant to test.**

Fixtures must be *derived from the production artifact* — the migration file,
the real entry point, the real image — never authored alongside the code that
consumes them. Concretely, as of `4b2c7bdd`:

- `ingestion_state_ddl()` lifts the `CREATE TABLE` **verbatim from
  `backend/migrations/001_init.sql`** and raises `SystemExit` if it cannot find
  it. It lives in `scripts/`, not on the production startup path.
- `test_the_sql_touches_only_real_columns` parses the migration and fails on any
  assignment or INSERT column the migration does not declare.
- The `FakeControlPool` doubles raise `UndefinedColumnError` the way PostgreSQL
  does, so a phantom column now fails in unit tests too.
- The supervisor path is exercised by calling `main()` **with no arguments**,
  resolving the pool the production way, patching only the venue transport.

And the verification that follows this document goes one step further: the
production **Dockerfile image**, a database built by the repository's **actual
migration runner**, and `main()` with no arguments inside that image.

## Wording note

Earlier I described the script's stream phases as closing "a live socket." They
did not. They were a **simulated transport backed by real database tests** — the
database was real Postgres, the venue transport was a fake. Corrected on the
record; a real venue connection has still not been established by any probe.
