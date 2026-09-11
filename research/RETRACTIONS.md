# Retractions — 2026-09-11

Owner instruction. Every figure below is **withdrawn from the record**. Where a
corrected value exists it is shown beside the old one; where none exists yet,
the status is INDETERMINATE and no replacement may be quoted.

---

## 1. Matched quantity — RETRACTED, missing-leg NULL bug

`mq = LEAST(max(CASE ...), max(CASE ...))` returned the **other leg's full
quantity** on a single-leg condition, because `LEAST` ignores NULL.

| population | OLD (retracted) | CORRECTED |
|---|---|---|
| ALL ELIGIBLE | ~~51,113,813~~ | **43,167,280** |
| BRIDGED | ~~10,492,139~~ | **9,908,017** |
| UNBRIDGED | ~~40,621,675~~ | **33,259,263** |

The old all-eligible figure was overstated by **7,946,533 shares / 15.5%**.

**Revalidated, unchanged** (run 69, corrected zero-safe arithmetic): eligible
matched cost **$42,578,503**, eligible matched gross P&L **$588,777**, matched
gross ROI **1.383%**, bridged **0.804%**, unbridged **1.556%**.

---

## 2. NON_MATCHED_REMAINDER ROI — RETRACTED, population mismatch

| | OLD (retracted) |
|---|---|
| BRIDGED | ~~−1.228%~~ |
| UNBRIDGED | ~~+1.234%~~ |

**Cause.** `sum(trading_pnl − mq·(1−pair_cost))` over
`sum(acq_cost − mq·pair_cost)`. On a single-leg condition `pair_cost` is NULL by
its own CASE guard, so both terms are NULL and `sum()` **skips the row**. Every
single-leg settled condition was silently excluded from **both** numerator and
denominator — and those are exactly the purely directional conditions, where
`M = 0` and *all* P&L is non-matched remainder.

`total_roi_pct` in the same row does **not** drop them. So matched, remainder and
total ROI were computed over **three different condition populations**, and the
required identity could not hold across them.

---

## 3. Verdict 4 — reset to INDETERMINATE

> `NON_MATCHED_REALIZED_MECHANISM_GENERALIZABLE?` = **INDETERMINATE**

Previously NOT SUPPORTED. **The old NOT SUPPORTED is not retained**, because its
load-bearing statistic — the sign flip across bridge status — was computed on
the invalid basis above. A dramatic result computed wrongly is not weak
evidence; it is **no evidence**, and keeping the verdict because the number was
striking would be reasoning from an artifact.

The other two grounds given at the time (settled-subset non-representativeness;
unquantified contamination) survive on their own merits but were **not** what
the verdict was rested on.

**Resolution rule, decided before the numbers arrive:**

- corrected non-matched economics still materially different or sign-unstable
  across bridge status → **NOT SUPPORTED**
- the divergence disappears → **withdraw that reasoning**
- settlement/attribution limits prevent a conclusion → **INDETERMINATE**

Data decides.

---

## 4. Path results — FROZEN, not retracted

Run 69's **quantity identities** are established (telescoping, single-leg,
monotonicity, reduction — all zero violations). But these remain
**uninterpreted** pending the timestamp-tie audit:

`FLIPPED` · `REBUILT` · reduction-event counts · peak path statistics

`(ts, id)` is deterministic **database** order, not **economic** order: `id` is
our insertion sequence across two ingestion paths, and a later `id` does not
prove a later fill. Telescoped `M` is invariant under reordering; path labels
are not.

`mirror_live=false`.
