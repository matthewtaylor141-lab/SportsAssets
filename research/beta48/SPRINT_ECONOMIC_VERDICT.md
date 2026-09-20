# SPRINT VERDICT: IS THERE A BETTOR STRATEGY WORTH TINY-CAPITAL VALIDATION?

Owner directive 2026-09-20: "optimize for TIME TO ECONOMIC EVIDENCE...
If the evidence is bad, tell me quickly."

**Answer: EVIDENCE_DOES_NOT_SUPPORT_TINY_CAPITAL_VALIDATION.**

The passive-maker hypothesis is falsified by a measurement that already
existed in this repository, confirmed by a live production measurement
taken today, and it cannot be rescued by the venue's verified maker
rebate because that rebate is arithmetically too small.

---

## THE ARITHMETIC THAT DECIDES IT

Per share, on the maker leg:

```
GROSS_EDGE (half-spread captured)              +0.0050
ADVERSE_SELECTION                              -0.0140
                                               -------
NET before fees                                -0.0090
  95% CI [-0.0143, -0.0038], clustered by condition, n = 9,337
```

Source: `research/beta48/MAKER_FIRST_FINDINGS.md` — 112,553 trades
across 9,337 independent conditions with one-hot settlement whose
`resolved_at` is strictly after the trade (1,684 already-resolved rows
dropped).

The venue's verified maker rebate cannot close that gap:

```
Fee = THETA * C * p * (1 - p),  THETA_MAKER = -0.0125   [PMUS, VERIFIED]

MAX rebate, at p = 0.50:   0.0125 * 0.25       = 0.003125/share (0.31 c)
BREAK-EVEN rebate required                     = 0.009000/share (0.90 c)
SHORTFALL                                      = 0.005875/share (0.59 c)
```

The rebate covers **34.7%** of the deficit. It is capped by the
`p(1-p)` term and is largest exactly at p = 0.50.

**Even at the most favourable end of the 95% CI, with the maximum
possible rebate applied, the strategy is still negative:**

```
-0.0038 + 0.003125 = -0.000675 per share
```

The entire confidence interval, shifted by the best available rebate,
remains below zero.

---

## THE LIVE CROSS-CHECK

Measured today against production, 1,644 observations over 980
distinct markets (spread <= 5c, mid 0.05..0.95):

```
median spread            0.0100
median HALF-spread       0.0050    <-- the gross maker edge
p25 / p75 spread         0.0100 / 0.0200
median mid               0.4450
```

The live PMUS half-spread (0.0050) matches the historical CLOB
`SPREAD_CAPTURE` (+0.0050) exactly. Two independent venues and
datasets agree on the gross term. Nothing in the live data suggests a
wider spread is available: the book is bimodal, and the wide half is
not a wide market but an EMPTY one.

```
A_TIGHT_LE_5C     2,011 obs   median spread 0.0100   median mid 0.39
B_WIDE_5_TO_20C     488 obs   median spread 0.1000   median mid 0.30
C_EMPTY_GT_20C    1,233 obs   median spread 0.8800   median mid 0.50
```

Regime C is books quoted 0.03/0.97 -- no liquidity at all. Pooling
them with genuine 1c markets produces a "median spread" of 0.94 in the
0.40-0.50 price band, which describes neither population.

---

## THE TAKER PAIR IS STRUCTURALLY NEGATIVE

Counted, not asserted, over 3,732 two-sided production observations:

```
observations   3,732
below par          0
at par             0
median basis  1.0400
min basis     1.0050
```

`ask + (1 - bid) = 1 + spread`. The cheapest pair observed cost half a
cent above par. There is no crossing arbitrage on this venue.

---

## WHY THE MAKER STRATEGY ALSO CANNOT BE PROSPECTIVELY VALIDATED

The frozen P_FILL evidence contract is **not satisfiable** from PMUS
public feeds (`bettor_evidence_matrix.py`, measured today):

- POSITIVE blocked by one fact: `PRINT_WAS_A_CLOB_EXECUTION_NOT_A_BLOCK`.
  The tape has no block flag and `block-trade-data.html` is HTTP 404.
- NEGATIVE blocked by five of seven, the binding one being
  `QUEUE_DEPLETION_FROM_CANCELLATIONS`. The book publishes SNAPSHOTS,
  not order events, and the tape's volume is market-wide and sideless,
  so the mix of trades and cancels cannot be recovered by subtraction.

So even a long prospective capture would produce mostly
`INTERVAL_CENSORED` rows, not fill labels.

---

## TURNOVER MAKES IT WORSE, NOT BETTER

- `BETA48_STATE.md` BLOCK_4: 22,297-share displayed bid queue against
  180 shares traded in 16 minutes, **zero touches**.
- `DECISION_REPORT_S10.md`: ~1 trade per 26 minutes per market; the
  bid-side queue implies **on the order of three hours to reach the
  front of the queue**.

A negative per-share edge realised a few times a day is a slow bleed,
not a business. And a positive edge at that turnover would not compound
either.

---

## LIMITATIONS, INCLUDING THE ONE THAT COULD CHANGE THE ANSWER

1. **Venue mismatch, stated plainly.** The -0.0140 adverse selection is
   measured on Polymarket **global CLOB**; the rebate is **PMUS**-
   verified. Mixing them is exactly what we normally refuse. It is done
   here as an argument *a fortiori*: the strategy is given the benefit
   of a favourable, unjustified cross-venue assumption and still fails.

2. **THE ONE THAT COULD RESCUE IT.** The CLOB rows exist because RN1 --
   an informed trader -- traded that token. `SELECTION_BIAS = SEVERE`.
   The flow that lifted the resting offer was therefore informed flow
   specifically, and a random resting order faces a mix of informed and
   uninformed. So -0.0140 may **overstate** the adverse selection a
   neutral maker would face. This is the single assumption whose
   failure would change the verdict, and it is not settled by anything
   on disk.

   What would settle it: adverse selection measured on a population
   that is NOT selected by an informed trader's actions. That requires
   either a different historical dataset (none exists on disk -- see
   the archaeology below) or BETTOR's own admitted fills, which
   requires an order path and capital.

3. **Sample independence.** 9,337 conditions, clustered CIs. Adequate.

4. **Fee rounding at tiny size.** Fees round to the nearest $0.01 with
   banker's rounding **per fill**. A 10-contract fill at p=0.445 earns
   $0.03; a 1-contract fill earns **$0.00**. Tiny-capital validation is
   the size at which the rebate is worth least.

---

## WHAT DOES NOT EXIST ON DISK

From the data archaeology (confirmed today):

- PMUS two-sided book (30 slugs, 6,952 obs) vs PMUS resolved outcomes
  (10,257 slugs): **INTERSECTION = 0**
  (`research/TRACK_P_DATA_ARCHAEOLOGY.md:15-19`).
- The only dataset pairing depth + executions + settlement is
  `u2_events_v1` + `settlement_v1`, and it is **ask-side only** on a
  different venue, selected by RN1's trades.
- No dataset on disk pairs an **observed resting bid with size**
  against a settled outcome, on either venue.

So no better historical replay is available today than the one already
performed.

---

## STRATEGY CLASSES COMPARED

| Class | Evidence | Verdict |
|---|---|---|
| A. Passive same-venue complementary pair | half-spread +0.0050 live and historical; adverse selection -0.0140; net -0.0090, CI excludes zero | **FALSIFIED at current evidence** |
| B. Maker first leg + controlled completion | same entry economics as A, plus residual inventory cost and ~3h queue time | **FALSIFIED (dominated by A)** |
| C. Taker complementary pair | basis 1 + spread; 0 of 3,732 below par | **FALSIFIED by arithmetic** |
| D. Phase X cross-venue PMUS/Kalshi | `TRACK_P_GATE = P-C`: +10.5% TRAIN -> +0.5% VALIDATION -> **-11.1% HOLDOUT**, CI [-15.5%, -6.6%], 1,170 markets, **at zero fees** | **FALSIFIED, locked, must not be retuned** |
| E. Directional taker on independent fair value | `FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED`; delta log loss -0.00926, `INCREMENTAL_SIGNAL_STATUS = NOT_DETECTED_AT_THIS_SAMPLE_SIZE` | **NOT_COMPARABLE** -- no edge estimate exists to compare |

---

## NO ML

Track 8's precondition is not met. Simple frozen cohorts show no
economic signal, labels are not defensible (the contract is not
identifiable), and there is nothing worth modelling. No model was
trained.
