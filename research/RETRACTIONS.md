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

## 2b. RESOLVED by run 71 — the corrected figures

Run 71 passed every gate: aggregate closure `0.000000`, per-condition closure
max gap `0.000000000` with **0 violations of 14,306**, cost closure `0.000000`
with **0 violations**, all five single-leg invariants **0** on 6,260 conditions,
and every term's row count identical (1,838 / 12,468 / 14,306) — **visible in
the output, not asserted**.

| | matched ROI | remainder ROI | total ROI |
|---|---|---|---|
| BRIDGED | 1.905% | **−1.568%** | 0.756% |
| UNBRIDGED | 1.944% | **+0.975%** | 1.569% |
| ALL SETTLED ELIGIBLE | 1.934% | 0.460% | 1.383% |

**Run 71 is closed.** The remainder arithmetic is not to be reopened unless
another upstream defect is found.

---

## 2c. ESTIMATOR HIERARCHY — which figure is primary, and why

**PRIMARY — the settlement-free matched mechanism.** Matched payoff does not
require settlement observation, so these are the primary matched-mechanism
figures:

| | matched gross ROI |
|---|---|
| ALL ELIGIBLE | **1.383%** |
| BRIDGED | **0.804%** |
| UNBRIDGED | **1.556%** |

**SECONDARY — the settled accounting decomposition** (§2b). It is *valid
accounting for the retained population*, but **not the primary estimator of the
general RN1 matched mechanism, because settlement retention is selected**
(verdict 2: 54.47% of conditions, retention non-monotonic at 65.72% → 39.01% →
86.99% across consecutive weeks).

### A numerical coincidence that must never be exploited

    ALL_ELIGIBLE_MATCHED_GROSS_ROI  = 1.383%   (settlement-free, matched only)
    ALL_SETTLED_TOTAL_TRADING_ROI   = 1.383%   (settled, total)

**Different numerators, different denominators, different populations.** The
equality is coincidence. Neither may ever be substituted for the other, and a
later reader finding "1.383%" in two places must check which one it is.

---

## 3. Verdict 4 — RESOLVED: NOT SUPPORTED

The verdict was reset to INDETERMINATE when its supporting statistic was
retracted, and a resolution rule was fixed **before** the corrected numbers
arrived. Run 71 supplied them; the first branch fires.

**The verdict, in the owner's words, which are binding and not to be
paraphrased:**

> **Verdict 4 — NON_MATCHED_REALIZED_MECHANISM_GENERALIZABLE: NOT SUPPORTED.**
> On the population-consistent settled accounting cohort, NON_MATCHED_REMAINDER
> is −1.568% for bridged conditions and +0.975% for unbridged conditions. This
> establishes that the remainder economics differ materially across these two
> retained cohorts. It does not establish that RN1 employs a different
> directional strategy in bridged markets. Mapper selection, settlement
> selection, and unobserved conversions/redemptions remain alternative
> explanations.

The earlier NOT SUPPORTED was **not** carried over: its load-bearing statistic
was computed on the invalid basis, and a dramatic result computed wrongly is not
weak evidence but **no evidence**. This verdict is re-earned on corrected
arithmetic, not restored.

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

---

## 5. Path results — section 4 SUPERSEDED for the FLIPPED boolean only

Run 72 proved the invariance of the `FLIPPED` boolean over **every** admissible
ordering, so the freeze in section 4 is lifted for that boolean and for nothing
else. The other names in that list — `REBUILT`, reduction-event counts, peak
path statistics — stay frozen, because a proof about *whether* a crossing
happens says nothing about *how many*, *how large*, or *in what order*.

### 5a. The primary behavioural result, locked in the owner's wording

> RN1 deploys the majority of capital into conditions whose directional exposure
> is provably observed on both sides of flat at least once: **7,924 / 26,248
> conditions**, carrying **$50.021M / 66.75%** of acquisition cost and
> **$37.094M / 78.97%** of matched cost. Flip behaviour is therefore
> concentrated in larger-capital conditions.

**Two wording limits are part of the result, not commentary on it.**

1. `PROVABLY_FLIPPED` proves **at least one** directional sign reversal, not
   repeated reversals. "Repeatedly" is not to be used until rebuild/flip-count
   invariance is established. I had used it; that was an overreach.
2. The 61.22% `DIRECTIONAL_ADD` notional statistic describes **flow
   composition**. It does not independently prove flipping, and it was wrong of
   me to present it as corroboration. Run 72 supplies the flip proof; that
   statistic supplies nothing to it.

### 5b. The 7,953 / 7,924 reconciliation

Two different quantities were being compared, and the difference is not an
error in either:

| figure | meaning | n |
|---|---|---|
| chosen-order `FLIPPED` | flips under the `(ts, id ASC)` tiebreak | 7,953 |
| `PROVABLY_FLIPPED` | flips under **every** admissible ordering | 7,924 |

The 29-condition gap is **derived from gates 2 and 3, not assumed**. Gate 2
found 0 conditions where `PROVABLY_FLIPPED` failed to flip under either
ordering, and gate 3 found 0 where `PROVABLY_NO_FLIP` flipped under either. So
every chosen-order flip outside the proven set must sit in the third class:

- **29** of the 50 `ORDER_NOT_PROVEN_INVARIANT` conditions flip under the chosen
  tiebreak only.
- **21** do not flip under it.
- **None of the 29 are proof-certified**, and none may be counted as such. They
  are reported inside `ORDER_NOT_PROVEN_INVARIANT` and nowhere else.

`ORDER_NOT_PROVEN_INVARIANT` remains 50 conditions / $121,605 / 0.162% of
acquisition cost — and the name still means *no proof either way*, never
*proven order-sensitive*.

`mirror_live=false`.
