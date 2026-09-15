# RUN 81 — REVISED DESIGN (81A PRIMARY, 81B SECONDARY)

**NOT APPROVED. NOT DISPATCHED.** This is the design for review. Run 81 does not
start until the owner says so. `mirror_live=false`. `ai_trades` / TRUEEDGE remain
untouched through run 83.

Carried forward unchanged: `AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z`; the two
estimators never merged; the five-stage evidence chain never collapsed; witness
counts on every test, with zero witnesses printing NOT TESTED and never PASS; an
UNKNOWN term making a result an UPPER BOUND and never a zero.

---

## 0. WHAT RUN 81 IS AND IS NOT

It measures, on the population runs 79–80.5 established:

> At the earliest moment BETTOR could realistically act, how much worse was the
> observable executable price than the price RN1 got?

It does **not** test TRUEEDGE's `lat_cost = $41,430.00`. That claim is a run-84
comparison and is not reproduced, targeted, or approached here. 81 produces its
own figures from independent evidence; whether they agree with the claim is a
later question asked once, at the end.

---

## 1. STATEMENT 0 — THE COHORT-DRIFT MEASUREMENT (new, cheap, first)

Run 80.5 and 80.5b built the settlement cohort from identical SQL at the same
pinned cutoff and disagreed by one event and $7.28 (RUN805_FINDINGS §8). U0 is
drift-free, so the drift is in a table the cutoff cannot pin.

Statement 0 measures it rather than assuming it:

- re-read U0 at the cutoff and assert it against run 80's 962,509 (the standing
  drift-free control);
- count `markets` rows satisfying `resolved AND resolved_at <= cutoff` now, and
  print it beside run 80.5b's 33,419-condition frame;
- count U2 events now and print it beside run 80's 214,708;
- print a `COHORT_STABILITY` verdict: `PINNED` where a count matches, `DRIFTING
  BY n` where it does not.

Every settlement-dependent figure in 81B is then stamped with the instant its
cohort was drawn. A drifting cohort is not a defect to fix — it is a property to
disclose.

---

## 2. RUN 81A (PRIMARY) — FIRST_RETAINED_OBSERVATION_DRAG

**Needs no clock and no settlement.** This is why it is primary: nothing in it
depends on `trades.ts` being a trustworthy absolute instant, on a C2↔C3 bridge,
or on `resolved_at`. It compares two prices on the same row.

### Population

U2 as run 80 measured it: **214,708 events / 17,775 conditions /
$48,336,268.86** — RN1 fills at the cutoff that carry a usable `copy_probes` row
(`book_ok`, no error, depth present). The probe's existence is the only binding
filter; every other predicate cost nothing.

### Strata, with the backfill fence held

`A_CHAIN`, `A_POLL`, `A_S1`, `A_ONLINE_COMBINED` (= chain + poll + s1), and
`A_BACKFILL_DIAGNOSTIC` standing alone. One-way pooling: backfill never enters
`A_ONLINE_COMBINED`, and no aggregate containing it may be called reactive
replicability or live first observation. Run 80 found `A_BACKFILL_DIAGNOSTIC`
empirically **empty (0 rows)**, so the fence costs nothing and stays anyway.

Any lane not in the matrix prints **HALT**.

### The decomposition, in the owner's form

```
TOP_OF_BOOK_MOVE(d)  = (best_ask_at_action(d) − p_h) · q
DEPTH_SLIPPAGE(d)    = (depth_walked_vwap(d) − best_ask_at_action(d)) · q
TOTAL_EXECUTION_DRAG = TOP_OF_BOOK_MOVE + DEPTH_SLIPPAGE
                     = (depth_walked_vwap(d) − p_h) · q
```

Reported per lane, never as a total printed beside its own components, with a
**closure assertion to ≤ $0.005 and a witness count** proving the two forms of
`TOTAL_EXECUTION_DRAG` agree row by row.

`d` is **not a time offset**. Run 79 proved there is no retained venue (C4)
timestamp and no independent C2↔C3 clock bridge over the mirror era. `d` is the
probe row itself — the first retained observation. Estimator A is silent about
when that was.

### The size question, bounded by observed depth

Run 80: depth arrays hit the eight-level retention cap on **90.04%** of probes,
and depth is exhausted at RN1's own size `q_a` on **0.14%**. So:

- at `q = q_a`, the retained book prices the walk on 99.86% of rows;
- at any hypothetical `q > q_a`, a row whose depth exhausts prints
  **DEPTH_EXHAUSTED = UNKNOWN**. It is never extrapolated, never filled with the
  last level's price, and never dropped silently — the count and notional of
  exhausted rows is printed beside every hypothetical-size figure, which makes
  that figure an **UPPER BOUND on replicability**, not a point estimate.

### The lane composition must be restated wherever A is quoted

U2 is **91.12% chain / 8.13% poll / 0.74% s1**. `chain` is the clock-corrupted
lane (91.7% negative lag). That does not damage Estimator A — A needs no clock —
but it does mean **A and B are nearly disjoint populations**, and no sentence may
put a lane-A dollar figure and a lane-B second figure in the same causal claim.

### Forbidden outputs

- "X seconds of latency caused $Y of drag" — in any phrasing.
- Any figure named "latency cost".
- A single combined A+B number.

---

## 3. RUN 81B (SECONDARY) — SETTLEMENT_REALIZED_CF_MARGIN AND WHAT SURVIVES IT

**Gated behind the quarantine and behind two disclosures.** 81B may not be read
as "RN1's edge" and may not be quoted without both.

### Cohort

The settlement cohort **after** `CONSERVATIVE_SETTLEMENT_TIMING_QUARANTINE`
(RUN805_FINDINGS §7): ≈112,315 events / ≈$24,677,543.62, restamped by statement
0 at 81's own instant. The rule is named in the output as a **timing
quarantine**, never as removal of corrupted payouts — §6b of RUN805_FINDINGS
found zero payout-vector defects in either arm.

### Disclosure 1 — settlement selection

Only **53.12% of events / 51.69% of notional** carry a settlement at all. The
settled cohort is not a random sample of U2; it is the subset that has resolved.
Every 81B figure prints the retained share beside it, and no 81B figure is
rescaled to U2 or to the whole book.

### Disclosure 2 — fees are UNKNOWN

No fee column exists anywhere in `backend/migrations/`. `IDENTIFIABLE_FEES` is
therefore **UNKNOWN**, not zero, which makes every `REMAINING_MARGIN` an
**UPPER BOUND**:

```
SETTLEMENT_REALIZED_CF_MARGIN = (payout − p_h) · q

REMAINING_MARGIN(d) = SETTLEMENT_REALIZED_CF_MARGIN
                    − TOP_OF_BOOK_MOVE(d)
                    − DEPTH_SLIPPAGE(d)
                    − IDENTIFIABLE_FEES            [UNKNOWN]
```

Printed as `REMAINING_MARGIN_UPPER_BOUND`, with the UNKNOWN term named in the
same row. The `$1` pair probe (task #126) is the only path to a measured fee and
is not a dependency of 81.

### Categorical separations held

- `SETTLEMENT_REALIZED_CF_MARGIN` is **not** `RN1_GROSS_EDGE` and is never
  called that.
- It is held categorically apart from `MATCHED_PAIR_GROSS_PNL = M·(1 − v_Y − v_N)`.
  The two are never summed, never compared as like quantities, and never appear
  in the same total.
- A structurally valid payout vector is not a *verified* payout. Whether the
  named winning outcome is the true one has no independent retained source; that
  UNKNOWN is carried into 81B and stated, not resolved.

---

## 4. ORDER, GATING, AND WHAT ENDS THE RUN

1. Statement 0 — cohort stability. If U0 has drifted from 962,509, **HALT**: the
   audit's one drift-free control has failed and nothing downstream is
   reproducible.
2. 81A in full, per lane, with the closure assertion and its witness count.
3. 81B only if 81A closed to the cent and statement 0 printed a cohort figure to
   stamp.
4. Witness ledger. Any test with zero witnesses prints NOT TESTED / NOT
   APPLICABLE. A zero violation count beside a zero witness count is never PASS.

Run 82 does not follow automatically. Findings return for review first.

---

## 5. WHAT 81 STILL CANNOT ANSWER

Stated up front so it is not discovered at the end:

- **When** BETTOR could have acted, as an absolute instant, on the chain lane —
  `CLOCK_UNRESOLVED` stands from run 79 and no run before 84 can change it.
- Whether the `endDate`-as-midnight mechanism *caused* the 1,902-condition timing
  anomaly — the per-row write branch is not recoverable.
- Whether the payouts on the quarantined cohort are *correct* — no independent
  settlement source is retained.
- Any fee, anywhere.
