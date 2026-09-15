# PROPOSED 81B POPULATION — FOR APPROVAL, NOT EXECUTED

**Nothing here has been run.** No settlement-realized margin, no remaining
margin, no matched-vs-directional split, no fees, no P&L. This document defines
a population and its disclosures; it computes no economics.

`mirror_live=false`. `ai_trades` / TRUEEDGE untouched.

---

## 1. THE POPULATION

```
RUN_81B_POPULATION = SETTLEMENT_ANALYZABLE
                     as defined in research/REACHABILITY_LADDER.md §F,
                     evaluated against AUDIT SNAPSHOT V1 and nothing else.

    112,543 events
      9,336 conditions
    $24,727,133.10 source notional
    52.441% of U2_SNAPSHOT_V1 events
    51.166% of U2_SNAPSHOT_V1 source notional
```

Inputs, exhaustively: `research/snapshots/u2_events_v1.jsonl.gz`,
`research/snapshots/settlement_v1.jsonl`, `research/snapshots/manifest_v1.txt`.
No database. `markets`, `market_tokens`, `copy_probes`, `ai_trades` and TRUEEDGE
are all out of scope, and the sealed-input checker already refuses them in any
`rn1_run81b*` SQL file should that path ever be taken.

## 2. WHAT THIS POPULATION IS, PERMANENTLY

> Events whose settlement is **analyzable from the sealed snapshot observed at
> `U2_SNAPSHOT_V1_DRAWN_AT`**.

It is **not**: what BETTOR knew; the database state at `AUDIT_CUTOFF_TS`;
settlement arrival; or a set whose payouts are known to be correct. Structural
well-formedness is not correctness — §F condition (4) proves a vector is an
array of the right length summing to one, and nothing more.

## 3. DISCLOSURES THAT MUST RIDE WITH EVERY 81B FIGURE

Each is a fact already measured, not a hedge:

1. **Selection: 47.6% of events and 48.8% of notional are not in it.** The
   dominant cause is `UNRESOLVED` (78,587 events / $19,910,944.73) — markets
   that had not resolved at the snapshot instant. No 81B figure may be rescaled
   to U2 or to RN1's whole book.
2. **`UNRESOLVED` is a property of the draw instant**, not of those events. A
   later snapshot would move it. Any 81B result is dated to
   `U2_SNAPSHOT_V1_DRAWN_AT`.
3. **17,822 events are `UNLINKABLE_TO_CONDITION_WITH_RETAINED_METADATA`** —
   within retained token metadata, not impossible in principle.
4. **The quarantine applied is weaker than the approved rule** (U2 witnesses
   only; see §F). It reproduces run 80.5 exactly on this cohort, which is an
   observation and not a guarantee.
5. **`IDENTIFIABLE_FEES` is UNKNOWN.** No fee column exists in any migration, so
   any margin net of fees is an **upper bound**, never a point estimate. The `$1`
   pair probe (task #126) is the only path to a measured fee and is not a
   dependency.
6. **Payout correctness is UNKNOWN.** Run 80.5b found zero payout-vector defects
   in either arm, and that is a statement about structure. Whether the named
   winning outcome is the true one has no independent retained source.

## 4. WHAT 81B WOULD COMPUTE, IF APPROVED

Stated so the scope is reviewable in advance. **None of it has been run.**

```
SETTLEMENT_REALIZED_CF_MARGIN = (payout − p_h) · q

REMAINING_MARGIN(d) = SETTLEMENT_REALIZED_CF_MARGIN
                    − TOP_OF_BOOK_MOVE(d)
                    − DEPTH_SLIPPAGE(d)
                    − IDENTIFIABLE_FEES            [UNKNOWN]
```

`payout` is `payouts[outcome_index(asset)]` from the sealed vector — the mapping
§F condition (6) already proves unambiguous on every row of this population.

Held apart, as before and permanently:

- `SETTLEMENT_REALIZED_CF_MARGIN` is **not** `RN1_GROSS_EDGE` and is never called
  that.
- It is categorically separate from `MATCHED_PAIR_GROSS_PNL = M·(1 − v_Y − v_N)`.
  The two are never summed, never compared as like quantities, never in one
  total.
- `REMAINING_MARGIN` prints as `REMAINING_MARGIN_UPPER_BOUND` with the UNKNOWN
  fee term named in the same row.
- The `DEPTH_EXHAUSTED` rule from 81A carries unchanged: a row whose retained
  depth cannot cover `q` is `NOT_IDENTIFIABLE_FROM_RETAINED_DEPTH`, never
  extrapolated, never imputed, never folded into an aggregate as if known.
- Nothing is compared against TRUEEDGE's `lat_cost`. That is run 84.

## 5. HOW IT WOULD RUN

Python over sealed bytes, no database connection — the same seal-by-construction
as 81A-S, and for the same reason: psql can load a local file only through
`\copy` or a temp table, both barred, so a SQL analysis would have to reach back
to live tables.

It would carry 81A-S's two validation habits, both of which have already earned
their place: **a second independent implementation** over the same sealed bytes
(Decimal vs float, different control flow — this is what caught the
NULL-in-a-Python-set defect), and a **per-row closure assertion** with witness
counts, where zero witnesses prints NOT TESTED rather than a passing zero.

## 6. THE DECISION IN FRONT OF YOU

1. Approve or amend this population.
2. Confirm the six disclosures ride with every figure.
3. Confirm the quarantine limitation in §3.4 is acceptable, or direct a stronger
   one — the only stronger version needs U0 in the snapshot, which would mean a
   V2 draw.

Nothing runs until you say so.
