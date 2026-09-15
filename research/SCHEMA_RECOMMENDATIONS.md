# Schema performance recommendations — recorded, NOT applied

Findings from the RN1 forensic work that point at the schema rather than at a
query. Nothing here has been applied. Each entry states what was observed, what
it cost, and what would have to be evaluated before it could go to production.

---

## 1. `copy_probes (trade_id)` — missing index (DEFERRED, owner 2026-09-11)

**Observed.** `research-sql` run 33 (2026-09-11 02:39Z) produced no results at
all: statement 1 hit the 600 s statement timeout, `ON_ERROR_STOP=1` aborted the
file, `psql exit=3`.

**Cause.** `backend/migrations/005_copy_probes.sql:9` declares

```sql
trade_id BIGINT REFERENCES trades (id),
```

and PostgreSQL does **not** create an index to back a foreign key constraint.
The only indexes on the table are `copy_probes (whale_id, probe_at DESC)` from
005 and `copy_probes (probe_at)` from 053. Neither serves a lookup by
`trade_id`.

**What it costs.** The natural way to ask "was this fill probed" is

```sql
EXISTS (SELECT 1 FROM copy_probes p WHERE p.trade_id = b.id AND p.book_ok ...)
```

which, without the index, is a sequential scan of `copy_probes` **per row**.
Against ~350k canonical RN1 fills that is ~350k full scans. The earlier
`exit=124` on `rn1_envelope_coverage_selection.sql` used the same pattern and
almost certainly died the same way.

**Worked around, not fixed.** The forensic queries now collect probed
`trade_id`s once into a set and hash-join, turning 350k scans into one. That is
sufficient for the analysis and required no schema change — the research surface
is read-only by construction and must stay that way.

**Status: DEFERRED. Do not add for the forensic run.** It is not needed to
finish the analysis.

**If it is ever promoted, evaluate first:**

- `CREATE INDEX CONCURRENTLY` only. A plain `CREATE INDEX` takes a lock that
  blocks writes to `copy_probes` for the duration, and the probe writer is on
  the live ingestion path — migration 053's own header already records this
  reasoning for the `probe_at` index.
- Write amplification. `copy_probes` is append-heavy on the fill path; every
  index is paid on every insert. Measure the insert rate before adding a third.
- Whether a partial index (`WHERE book_ok AND best_ask IS NOT NULL`) serves the
  real query shapes at lower cost than a plain one.
- Whether any production read actually needs it, or only analysis does. If only
  analysis, the hash-join rewrite above may be the whole answer.

---

## 2. No market-open metadata for Polymarket conditions (OBSERVED, no action)

`markets` (`backend/migrations/001_init.sql:27`) carries `condition_id`,
`title`, `slug`, `event_slug`, `event_title`, `sport`, `tags`, `closed`,
`resolved`, `resolved_prices`, `resolved_at`, `updated_at`. There is **no**
creation, open, or start timestamp, and `updated_at` is `DEFAULT now()` on our
own upsert — it records when *we* last wrote the row, not when the market
existed.

**Consequence for analysis.** A question of the form "did RN1 hold anything
before his first retained fill" cannot be answered from market metadata, because
there is no point in time at which the condition is known to have begun. The
Check B initialization work therefore does not attempt it; it anchors on an
independent position snapshot and replays forward instead.

No recommendation attached. Recorded so the next person does not spend time
looking for a column that is not there.
