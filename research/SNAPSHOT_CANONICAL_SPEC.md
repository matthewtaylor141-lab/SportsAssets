# CANONICAL SERIALIZATION SPECIFICATION — FROZEN_U2_CONDITION_UNIVERSE

**Fixed before the draw. Never changed after.** Changing any rule below changes
every hash and breaks the pin, so a change means a new snapshot with a new name,
not an edit to this one.

Extractor: `research/gen/draw_settlement_snapshot.sql` (one statement).
Workflow: `.github/workflows/research-snapshot.yml` (read-only).

---

## 0. NOT YET DRAWN

Nothing has been extracted. This document and the extractor are for review. The
one open question in §9 must be answered first, because the answer changes what
is drawn and a second draw would produce a second competing "immutable"
snapshot.

---

## 1. THE UNIVERSE (decision 1)

```
FROZEN_U2_CONDITION_UNIVERSE
  = DISTINCT trades.condition_id
    over the U2 predicate at AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z
    WHERE condition_id IS NOT NULL
```

The U2 predicate is byte-for-byte the one run 81A used: RN1's trades with
`ts <= cutoff` and `detected_at <= cutoff`, joined to a `copy_probes` row with
`probe_at <= cutoff`, `book_ok`, `error IS NULL`, `depth IS NOT NULL`. No price
filter, no depth-level filter.

**Settlement status is an attribute inside the universe, never a filter on it.**
Resolved, unresolved, no `markets` row, and no token metadata are all carried.
`#U2_EVENTS_WITH_NULL_CONDITION` counts the trades that name no condition; they
cannot belong to a condition universe and are reported rather than dropped
silently.

## 2. ABSENCE IS FROZEN (decision 2)

The universe is built first and the mutable tables are `LEFT JOIN`ed onto it, so
a condition is never absent from the snapshot:

| flag | meaning |
|---|---|
| `market_row_present` | a `markets` row existed at draw |
| `token_metadata_present` | at least one `market_tokens` row existed at draw |
| `token_count` | how many, or `null` when absent |
| `resolved`, `resolved_at`, `resolved_prices_type`, `payout_len`, `payouts` | `null` throughout when the row is absent |

A production row that appears after the draw cannot enter 81B: the condition is
already in the snapshot, marked absent, and 81B reads the snapshot.

## 3. ATOMICITY (decision 3)

The extractor is **one SQL statement**, so the universe, `markets` and
`market_tokens` are read from one transaction snapshot by construction — there
is no window between reads at all. The workflow additionally runs it under
`psql -1` with `default_transaction_isolation=repeatable read` and
`default_transaction_read_only=on`.

The isolation actually in force is **recorded in the artifact**, read from the
server (`current_setting('transaction_isolation')` and `transaction_read_only`),
not asserted in prose. `snapshot_drawn_at` is `now()`, which in Postgres is the
transaction start instant and so describes the same snapshot as the data.

## 4. CANONICAL RENDERING (decision 4)

| # | rule | fixed as |
|---|---|---|
| 1 | row order | `ORDER BY condition_id COLLATE "C"` — byte order, so the result does not depend on the server's locale or collation version |
| 2 | timestamps | UTC, `to_char(t AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')` — fixed 6 fractional digits, always, trailing `Z` |
| 3 | NULL | JSON `null`, one representation, for every absent value including absent rows |
| 4 | booleans | JSON `true` / `false`, one representation |
| 5 | payouts | fixed-scale **strings**: `to_char(v, 'FM9999990.000000')`. Strings, not JSON numbers, so no numeric renormalization can occur between draw and read |
| 6 | arrays | payouts in stored array order (= the outcome index the payout vector is indexed by); tokens `ORDER BY outcome_index NULLS LAST, token_id COLLATE "C"` |
| 7 | text | JSON string escaping produced by `json_build_object` |
| 8 | key order | explicit in `json_build_object`. **`jsonb` is deliberately not used for the row object** — its key order is internal (length, then bytes) and would be a rule this file does not control |

Key order, fixed:

```
condition_id, market_row_present, market_slug, event_slug, sport, closed,
resolved, resolved_at, resolved_prices_type, payout_len, payouts,
token_metadata_present, token_count, tokens
```

Token object key order, fixed: `outcome_index, token_id, outcome`.

## 5. FILE FORMAT

`psql -A -t -X` — unaligned, tuples-only, no column headers — so stdout **is**
the artifact, one line per row:

```
#KEY<TAB>VALUE          header lines, in the fixed order below
{"condition_id": ...}   one canonical JSON object per condition, ordered by rule 1
```

Header keys, in order: `SNAPSHOT_FORMAT`, `SNAPSHOT_NAME`, `SNAPSHOT_MEANING`,
`AUDIT_CUTOFF_TS`, `SNAPSHOT_DRAWN_AT`, `TRANSACTION_ISOLATION`,
`TRANSACTION_READ_ONLY`, `SNAPSHOT_U2_CONDITION_COUNT`,
`SNAPSHOT_U2_EVENT_COUNT`, `U2_EVENTS_WITH_NULL_CONDITION`, `SNAPSHOT_ROW_COUNT`,
`GUARD_COUNT_MISMATCH`, `GUARD_PAYOUT_NON_NUMBER`,
`GUARD_PAYOUT_ROUNDTRIP_LOSSY`, `GUARD_ROW_HAS_NEWLINE`,
`DB_CANONICAL_CONTENT_SHA256`, `DB_CANONICAL_CONTENT_SCOPE`.

## 6. HASHING — TWO HASHES, NEITHER OVERSOLD

| name | computed by | covers |
|---|---|---|
| `DB_CANONICAL_CONTENT_SHA256` | the database, inside the same statement | **the data lines only**, newline-joined, no trailing newline, in canonical order |
| `COMMITTED_FILE_SHA256` | `sha256sum` in the workflow, after the draw | **the whole committed file**, header lines included |

They are different bytes and are never conflated. `DB_CANONICAL_CONTENT_SCOPE`
states in the artifact itself exactly what the database hashed, so nobody later
reads the DB hash as authenticating the header.

**Both are reproducible offline** from the committed file:

```
# DB_CANONICAL_CONTENT_SHA256
grep -v '^#' snapshot.txt | perl -0pe 's/\n\z//' | sha256sum

# COMMITTED_FILE_SHA256
sha256sum snapshot.txt
```

Also emitted: `SNAPSHOT_ROW_COUNT` and `SNAPSHOT_U2_CONDITION_COUNT`.

## 7. GUARDS — inside the artifact, so they describe the same snapshot

The workflow refuses to publish if any is non-zero:

| guard | fires when |
|---|---|
| `GUARD_COUNT_MISMATCH` | data rows ≠ distinct U2 conditions |
| `GUARD_PAYOUT_NON_NUMBER` | a payout array element is not a JSON number |
| `GUARD_PAYOUT_ROUNDTRIP_LOSSY` | a payout's value changes under rule 5's fixed-scale rendering |
| `GUARD_ROW_HAS_NEWLINE` | a rendered row contains a newline, tab or carriage return, which would break one-line-per-row |

Rule 5's losslessness is therefore **tested on the actual data**, not assumed
from the format string.

## 8. STRUCTURAL_BINARY_ELIGIBILITY

The snapshot carries the **ingredients**, not a precomputed verdict, so 81B
derives the flag and the derivation is reviewable rather than baked in:
`token_count`, the token list with `outcome_index` and `outcome`, `payout_len`,
the `payouts` array in outcome order, `resolved_prices_type`, and the two
presence flags. Everything run 80.5b's payout-integrity statement tested is
derivable from these without touching a live table.

## 9. THE OPEN QUESTION — a condition snapshot does not make 81B reproducible

**This needs your decision before the draw, and it is the reason nothing has run
yet.**

Decision 1 says "every distinct condition represented in the frozen Run-81A U2
population." **There is no frozen Run-81A U2 population.** 81A emitted
aggregates — counts, sums, percentiles — not the list of trade ids or condition
ids behind them. That list was never materialised anywhere, so it cannot be
recovered. The universe below will be drawn from `copy_probes` **as it stands at
draw time**, and the header will print its condition count against 81A's 17,755
so any shortfall is measured rather than hidden.

That is survivable at condition level: a condition leaves U2 only when *every*
probe on it has aged past 37 days, so condition-level erosion is far slower than
event-level. It is **not** survivable at event level, and that is the gap:

> **81B's settlement cohort is a set of EVENTS, and the event population is the
> thing actually being deleted** — 57 events in 1 h 48 m (RUN81A_FINDINGS §0).
> Freezing settlement metadata makes the *payout side* of 81B reproducible and
> leaves the *population side* eroding underneath it. Re-running 81B a week
> later would return different dollar totals from an identical snapshot.

Three options, and this is your call:

**(a) Condition snapshot only, as approved.** Cheapest, ~17,775 rows. 81B's
payout mapping is frozen; its event population is not, and every 81B figure must
be stamped with its draw instant and re-derived if re-run. Honest but not
reproducible in the sense Decision 1 is reaching for.

**(b) Add a frozen U2 event manifest to the same draw.** One extra statement in
the same transaction emitting `trade_id`, `condition_id`, `ts`, `size`, `price`,
`notional`, `source`, and the probe's `best_ask` and `depth` — roughly 214,651
rows. That makes 81A **and** 81B fully reproducible from committed bytes and
ends the erosion problem for the whole audit. It is a larger committed artifact
(tens of MB with the depth arrays; a few MB without them), and it is a scope
expansion you did not approve, so I have not built it.

**(c) (b) without the depth arrays.** Freezes the population and the top-of-book
inputs; leaves depth-walked VWAP dependent on live `copy_probes` and so still
eroding. A middle that keeps the file small but only half-solves it.

My recommendation is **(b)**, because the cost of getting it wrong is asymmetric:
a larger commit is recoverable at any time, and deleted probes are not. The
clock is running at roughly 32 events an hour.

I have not expanded the draw on my own. Say which, and I will finish the
extractor and run it.
