# RN1 FULL U0 EXTRACTION — OPERATOR RUNBOOK

**This session did not run any of this. No credentials were sought, held or used.
No production system was contacted. This file is a deliverable, not a record.**

You do not need to understand the research to run this. Every scientific choice
has already been made and written down below. If a step does not behave exactly
as described, **stop and report it** — do not adapt, do not substitute, do not
retry with a changed query. An extraction that quietly differs from this
specification is worse than no extraction, because it will be trusted.

---

## 1. Why this is being asked for

The retained research corpus holds **214,609** RN1 fills. RN1 actually had
**962,509** trades at the audit cutoff. The retained file is the 22.3% that
happened to carry a copy probe, because the snapshot's population filter was a
JOIN to `copy_probes` rather than a scan of `trades`.

That gap is not a sampling inconvenience. Reconstructing a matched position needs
**every** fill in the market, so a condition with one missing fill is not
partially usable — it is unusable. Only **9,269 of 17,752** conditions (52.2%)
have a complete fill history in the retained file.

Worse, the 52.2% that survive are **not a random half**. Completeness requires
every trade to have been probed, so the filter selects against heavily traded
conditions — exactly the conditions where pairing behaviour concentrates.
Measured standardised mean differences between complete and incomplete
conditions:

| feature | SMD | effect |
|---|---|---|
| `u0_trade_count` | −0.776 | LARGE |
| `sides_seen` | −0.690 | LARGE |
| `u2_fill_count` | −0.564 | LARGE |

Because of that, **no result computed on the retained corpus may be described as
a fact about RN1**. This extraction is what would make such a statement possible.

## 2. What you need before starting

- Read access to the production database that holds the `trades` table.
- Roughly **1–2 GB** of free disk for the output.
- Nothing else. No write access. No application credentials. No API keys.

If you have been given write access, you have been given too much; ask for a
read-only role instead.

## 3. Bind these two values

Everything below is parameterised on exactly two values. Use these:

```
:cutoff_ts  = 2026-09-12T00:00:00Z
:whale_ids  = ARRAY['RN1']
```

The cutoff is the snapshot's own `AUDIT_CUTOFF_TS` from
`research/snapshots/manifest_v1.txt`. **Do not use "now".** Using a later cutoff
produces a file that cannot be compared to the retained corpus, which defeats the
purpose.

## 4. Step one — the count query. Run this first, on its own.

Do not skip this. It is what tells you whether the column names in this document
match the live schema, before you spend an hour on a full extract.

```sql
SET TRANSACTION READ ONLY;

SELECT whale_id,
       count(*)                              AS fills,
       count(DISTINCT condition_id)          AS conditions,
       min(ts)                               AS first_ts,
       max(ts)                               AS last_ts,
       count(*) FILTER (WHERE side = 'SELL') AS sell_fills
FROM trades
WHERE ts <= :cutoff_ts AND detected_at <= :cutoff_ts
GROUP BY whale_id
ORDER BY fills DESC;
```

**Read the RN1 row and check three things:**

1. `fills` should be **approximately 962,509**. A materially different number
   means the live population filter differs from the snapshot spec — **stop and
   report the number you got.** Do not adjust the query to make it match.
2. `conditions` should be **approximately 17,752**.
3. `sell_fills` — write it down. It is not a pass/fail check, but it changes what
   the data can answer (see §8).

If a column does not exist, the schema has moved. Report the error text verbatim
and stop. Do not guess a replacement column name.

## 5. Step two — the extraction

```sql
-- RN1 full U0 extraction. READ ONLY.
SET TRANSACTION READ ONLY;

SELECT
    t.whale_id,
    t.id                AS trade_id,
    t.order_id,
    t.condition_id,
    t.asset             AS token_id,
    t.outcome_index,
    t.outcome,
    t.side,
    t.price,
    t.size,
    t.notional,
    t.ts,
    t.detected_at,
    t.maker             AS maker_or_taker,
    t.market_slug
FROM trades t
WHERE t.whale_id      = ANY(:whale_ids)
  AND t.ts          <= :cutoff_ts
  AND t.detected_at <= :cutoff_ts
ORDER BY t.whale_id, t.condition_id, t.ts, t.id;
```

Write it to a compressed JSONL or CSV file. One row per fill, no aggregation, no
deduplication, no rounding of `price` or `size`. If your client truncates long
numeric fields, fix the client rather than the query.

```
suggested name:  rn1_u0_full_v1_<YYYYMMDD>.jsonl.gz
```

**`whale_id` must be on every row.** It is the only field that makes the file
self-describing. The retained corpus has no account column at all, and its
single-account status is known only from the document that produced it — a
situation we are not repeating.

## 6. Step three — the five acceptance checks

Run all five. Report every number, including the ones that pass.

| # | check | pass condition |
|---|---|---|
| 1 | row count | ≈ 962,509 for RN1 |
| 2 | every `trade_id` in `research/snapshots/u2_events_v1.jsonl.gz` appears in the extract | 100% |
| 3 | per condition, extract fill count ≥ retained fill count | every condition |
| 4 | `whale_id` non-null | every row |
| 5 | `condition_id`, `token_id`, `side`, `price`, `size`, `ts` non-null | every row |

**Check 2 is the important one.** The retained corpus is supposed to be a subset
of the extract. If a retained `trade_id` is missing from the extract, the two
were drawn from different populations and neither can be compared to the other.
Report the count of missing ids and a sample of ten.

## 7. What must not happen

- **No writes.** No `CREATE`, no `INSERT`, no `UPDATE`, no temp tables, no
  materialised views. `SET TRANSACTION READ ONLY` is in both queries for this
  reason; do not remove it to "make it faster".
- **No sampling.** No `LIMIT`, no `TABLESAMPLE`, no "just the last 90 days". A
  sampled extract recreates the exact problem this is meant to solve.
- **No filtering by market, sport, league or size.** Every fill or none.
- **No deduplication.** If the table has duplicate rows, that is a finding and it
  belongs in the extract where it can be seen.
- **Do not run this against a replica that lags the cutoff.** Check replica lag
  first; if it lags, use the primary or report that you cannot.

## 8. One thing to flag when you report back

The retained corpus is **BUY-only**. If your count query showed
`sell_fills > 0` for RN1, then production holds sells that the research corpus
never saw, and three quantities currently recorded as `NOT_IDENTIFIED` become
measurable: voluntary exits, reductions that were not made by buying the
complement, and realised exit markouts.

Say so explicitly in your report. It changes what the next piece of work is.

## 9. What to send back

1. The output file, and its `sha256`.
2. The full count-query result (all accounts, not just RN1).
3. All five acceptance-check numbers.
4. The `sell_fills` figure for RN1.
5. Anything that did not match this document, quoted verbatim.

---

*Column names above were read from `research/rn1_causal_ledger.sql` and
`research/SNAPSHOT_CANONICAL_SPEC.md`, not from a live schema, because no
database was contacted. Step one exists to catch that.*
