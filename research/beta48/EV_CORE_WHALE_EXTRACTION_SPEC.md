# EV CORE — WHALE FILL EXTRACTION SPECIFICATION

**READ-ONLY. NOT EXECUTED. NO CREDENTIALS USED. NO PRODUCTION CONTACT MADE.**

This document and the query below exist so that an authorised operator can
recover the full whale fill corpus later. Nothing here was run.

---

## Why this is needed

The offline research corpus (`research/snapshots/u2_events_v1.jsonl.gz`) holds
**214,609** rows. Its own manifest records that RN1 had **962,509** trades at the
audit cutoff. The retained file is the subset that carried a copy probe —
**22.3%** — because the population filter was a JOIN to `copy_probes`, not a
scan of `trades`.

That subsetting is fatal to any cumulative-position reconstruction, because
matched inventory needs *every* fill. Only **9,269 of 17,752** conditions (52.2%)
have a complete fill history in the retained file; the rest are partial and
their pair statistics are invalid.

Recovering the other 77.7% of fills is the single highest-value data action
available, and it needs a database read, not new collection.

## What is missing, precisely

| | retained corpus | production |
|---|---|---|
| RN1 trades | 214,609 (22.3%) | 962,509 |
| price / size per fill | yes | yes |
| book snapshot per fill | yes (probe only) | probe only |
| **account identifier** | **absent** | `whale_id` |
| other whale accounts | none | Ferrari, SwissTony, HomeRunHazard, kch123, w2c33 |

The retained corpus carries **no account column at all**. Its single-account
status is known only from the spec that produced it, not from the data.

## Required fields

Minimum for pair reconstruction and action-EV training:

```
whale_id            the account. WITHOUT THIS, NOTHING ELSE IS SAFE TO USE.
trade_id            fill identity, for dedup against the retained corpus
order_id            groups partial fills of one order, where available
condition_id        the binary market
asset / token_id    WHICH LEG - required; a condition alone cannot pair
outcome_index       the venue's 0/1 token index
outcome             the token's label
side                BUY / SELL - the retained corpus is BUY-only and the
                    production table may not be
price               per-share execution price
size                shares
ts                  venue trade time (the as-of clock)
detected_at         our observation time, for latency and as-of discipline
maker_or_taker      where available; needed for any fill-hazard work
market_slug         canonical event mapping
```

## The read-only query

No writes, no DDL, no temp tables. `SET TRANSACTION READ ONLY` is included so
the statement cannot mutate anything even by accident.

```sql
-- EV_CORE whale fill extraction. READ ONLY. Run by an authorised operator.
-- Bind :cutoff_ts and :whale_ids before executing.
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
WHERE t.whale_id = ANY(:whale_ids)
  AND t.ts          <= :cutoff_ts
  AND t.detected_at <= :cutoff_ts
ORDER BY t.whale_id, t.condition_id, t.ts, t.id;
```

Companion count, to verify completeness before trusting any reconstruction:

```sql
SET TRANSACTION READ ONLY;

SELECT whale_id,
       count(*)                          AS fills,
       count(DISTINCT condition_id)      AS conditions,
       min(ts)                           AS first_ts,
       max(ts)                           AS last_ts,
       count(*) FILTER (WHERE side = 'SELL') AS sell_fills
FROM trades
WHERE ts <= :cutoff_ts AND detected_at <= :cutoff_ts
GROUP BY whale_id
ORDER BY fills DESC;
```

`sell_fills` matters: the retained corpus is BUY-only, and if production holds
sells then voluntary exits, reductions-not-via-complement and realised exit
markouts all become measurable — the three components currently scoped as
`NOT_IDENTIFIED`.

## Column names are unverified

The names above are read from `research/rn1_causal_ledger.sql` and the snapshot
spec, **not from a live schema**, because no database was contacted. An operator
should run the count query first and adjust names before the extraction.

## Acceptance checks before the extract is trusted

1. `fills` for RN1 at the same cutoff should be ≈ **962,509**. A materially
   different number means the population filter differs from the snapshot spec.
2. Every `trade_id` in the retained corpus should appear in the extract.
3. Per condition, the extract's fill count should be ≥ the retained count.
4. `whale_id` must be non-null on every row; a null account is unusable.
5. If `sell_fills > 0`, the pair reconstruction needs its SELL branch built
   before use — it currently skips and counts them.

## What stays forbidden

No credentials. No production contact. No writes. This specification is the
deliverable; execution is an authorised operator's decision, not this session's.
