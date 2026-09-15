# TRACK P — NATIVE PREDICTIVE PROFITABILITY

**Gate: `P-C — NO VALIDATED PREDICTIVE EDGE`**

Frozen spec `32cdcc7e3cd5f1aa0a702ff3c4ce2c2e43c9f9b3c924cfcc8c04c5deece28a88`,
hashed before the holdout was opened. Offline throughout; no venue contact,
no credential, no production read or write.

## Data inventory

| | |
|---|---|
| DATASET | `AUDIT_SNAPSHOT_V1` — `u2_events_v1` + `settlement_v1` |
| SOURCE | `research/snapshots/`, drawn 2026-09-12T02:53:56Z, read-only repeatable-read |
| DATE RANGE | 2026-08-06 .. 2026-09-11 (35 days) |
| SPORTS | Soccer 34.2%, Tennis 33.1%, unclassified 10.2%, Other 7.6%, Non-Sports 5.5%, MLB 4.7%, NFL 4.2%, NBA 0.4%, MMA 0.2% |
| OBSERVATIONS | 214,609 (trade, probe) pairs / 17,752 conditions |
| SETTLED MARKETS | 17,752 |
| PRICE HISTORY | **NO** — one ask ladder per probe, not a time series |
| EXECUTABLE PRICE AT T | **YES** — `best_ask` on 100% of rows |
| BOOK DEPTH | **YES** — ask ladder, median 8 levels, 100% of rows |
| GAME START TIME | **NO** — field absent on every row |
| LIVE/PREGAME | **NO** — no flag, no start time; the whole axis is `NOT_IDENTIFIED` |
| OUTCOME | **YES** — `condition_id` → settlement payouts |
| SAFE_FOR_MODELING | **YES** for calibration on this population, bias declared. **NO** for any claim about the whole board. |

**Coverage gaps.** No price series ⇒ no drift, no closing line, no market
movement. No start time ⇒ pregame vs live cannot be separated. No
market-type field. **Ask side only** ⇒ no spread, no sell-side execution.

**Selection bias — the big one.** The sampling frame is *moments RN1
traded*. Not the tradable universe, not a random sample of it. Every rate
below is conditional on RN1 having just bought that token.

**RN1 containment.** `side, size, price, notional, his_price, his_size,
his_notional, reaction_s, trade_id, detected_at, source` are banned as
features — asserted in code at load time, not promised in a comment. The
only inputs are `best_ask` at `probe_at` and `sport`.

**Leakage.** Every feature is observed at `probe_at`; the label is
settlement. 1,684 rows whose market had already resolved when observed
were dropped. 0 rows had unknown resolution time.

## Decision set and splits

113k modellable rows collapse to **13,730 decisions / 8,981 independent
markets** — the first eligible probe per (condition, token). RN1 probed the
same token repeatedly; counting rows as the sample size would overstate the
evidence by an order of magnitude.

```
TRAIN       2,260 decisions  1,533 markets  08-06..08-21  base rate 0.4832
VALIDATION  3,946 decisions  2,594 markets  08-21..08-31  base rate 0.4878
HOLDOUT     7,524 decisions  4,854 markets  09-01..09-11  base rate 0.4773
```

Chronological, declared in source before any result, and assigned per
condition so no game straddles a boundary.

## Q1 — is the ask miscalibrated? (TRAIN)

| ASK BAND | N | MEAN_ASK | SETTLE | SETTLE−ASK |
|---|---|---|---|---|
| [0.02,0.10) | 148 | 0.0579 | 0.0473 | **−0.0106** |
| [0.10,0.20) | 195 | 0.1461 | 0.1231 | **−0.0230** |
| [0.20,0.30) | 231 | 0.2458 | 0.2511 | +0.0052 |
| [0.30,0.40) | 259 | 0.3449 | 0.3320 | **−0.0128** |
| [0.40,0.50) | 278 | 0.4469 | 0.4101 | **−0.0368** |
| [0.50,0.60) | 287 | 0.5448 | 0.5331 | **−0.0117** |
| [0.60,0.70) | 226 | 0.6455 | 0.6239 | **−0.0216** |
| [0.70,0.80) | 237 | 0.7427 | 0.6920 | **−0.0507** |
| [0.80,0.90) | 214 | 0.8448 | 0.8084 | **−0.0364** |
| [0.90,0.98) | 175 | 0.9357 | 0.9257 | **−0.0100** |

**The ask sits ABOVE the settlement rate in 9 of 10 bands.** This is the
answer to the first question, and it is the opposite of the one we wanted:
on this population, paying the ask and holding to settlement is
systematically negative before a cent of fees. The venue is on the right
side of its own spread, which is what a functioning book looks like.

## Model grid — 32 hypotheses, all shown

`A_MARKET_ONLY` and `C_LOGISTIC_ON_LOGIT_ASK` produced **zero** trades at
every threshold: neither ever believed fair value exceeded the ask by even
0.5pp. `D_ISOTONIC_ON_ASK` traded and lost at every threshold. Only
`E_ISOTONIC_BY_SPORT @ 0.03` was positive on validation (**ROI +0.0048**,
639 markets) — that cell was selected by the pre-declared rule (highest
validation ROI among cells with ≥200 independent markets) and frozen.

## Primary result

| | TRAIN | VALIDATION | **HOLDOUT** |
|---|---|---|---|
| TRADES | 387 | 684 | **1,261** |
| INDEPENDENT MARKETS | 367 | 639 | **1,170** |
| CAPITAL DEPLOYED | 200.96 | 329.41 | **624.01** |
| GROSS PNL | +25.35 | +9.60 | **−54.60** |
| NET PNL | +21.04 | +1.59 | **−69.01** |
| **ROI** | +0.1047 | +0.0048 | **−0.1106** |
| ROI 95% CI | [+0.024, +0.185] | [−0.057, +0.069] | **[−0.155, −0.066]** |
| WIN RATE | 0.5736 | 0.4839 | 0.4401 |
| AVG EXPECTED EDGE AT ENTRY | +0.0588 | +0.0587 | +0.0584 |
| AVG REALIZED RETURN | +0.0544 | +0.0023 | **−0.0547** |
| MAX DRAWDOWN | −4.93 | −10.91 | **−70.14** |
| WORST DAY / WEEK | −1.40 / +3.82 | −5.73 / −1.49 | −17.89 / −45.16 |
| POSITIVE / NEGATIVE MONTHS | 1 / 0 | 1 / 0 | **0 / 1** |
| BRIER | 0.1801 | 0.1858 | 0.1827 |
| LOG LOSS | 0.5302 | 0.6110 | 0.5941 |
| CALIBRATION ERROR (ECE) | 0.0104 | 0.0190 | 0.0241 |

The decay across three chronological splits — **+10.5% → +0.5% → −11.1%** —
with the model claiming a *constant* +5.9pp expected edge at entry in all
three, is the signature of a calibrator fitting in-sample noise. The
holdout confidence interval lies entirely below zero on 1,170 independent
markets.

## Concentration and robustness

Profit concentration does not rescue it; it makes it worse. Excluding the
best holdout trade: −69.88 (ROI −0.1120). Excluding the best five: −73.37
(−0.1177). The loss is broad, not one bad outcome.

Robustness (strategy not redesigned):

| DEGRADATION | TRADES | NET PNL | ROI |
|---|---|---|---|
| ask +0.00 | 1,261 | −69.01 | −0.1106 |
| ask +0.01 | 961 | −61.49 | −0.1236 |
| ask +0.02 | 723 | −56.99 | −0.1524 |
| ask +0.03 | 500 | −45.38 | −0.1805 |
| **no fee at all (gross)** | 1,261 | **−54.60** | **−0.0896** |

It is negative with **zero** costs. The fee assumption is not what killed
it, so no fee verification could revive it.

`CAPACITY = PARTIAL` — median top-of-book 1,100 contracts on holdout
trades, but one snapshot per market on an RN1-selected population.

Per $1 deployed: **−0.1106**. Per $100: **−11.06**. Per 1,000 decisions:
**−54.73**.

## Gate

```
P-C — NO VALIDATED PREDICTIVE EDGE
```

The properly controlled holdout does not support positive net expectancy.
Not P-D: the data was sufficient to run the test. Not P-E: no leakage or
integrity defect was found — the audit is in the report.

## What failed, precisely

Not "the model was too simple". The **market-only** and **logistic**
baselines never found a single contract whose fair value exceeded its ask,
across 8 thresholds. The only model that traded was the one flexible
enough to fit noise, and its edge evaporated in order across time. The
underlying fact is the Q1 table: **on this population the ask is above the
settlement rate almost everywhere**, so a taker holding to settlement
starts behind and stays there.

### The mirror image is not a free lunch

If the ask is systematically too high, the natural thought is to be the
seller instead. That is not available here and should not be dangled as
though it were. We hold the **ask side only** — no bid — so sell-side
execution cannot be modelled at all. And the tradable analogue, buying the
complementary token, runs into Track B-L's locked **FINDING B-1**: a
taker/taker complementary pair costs `1 + spread`, a guaranteed loss before
fees. The same book structure that makes the ask too high makes the other
ask too high.

## One genuinely independent next hypothesis

Everything above tests one thing: *pay the ask, hold to settlement*. The
single hypothesis it does **not** touch, and that the same data cannot
answer, is whether BETTOR can price a contract better than the market **at
the bid** — i.e. as a resting maker rather than a taker. That needs data
this snapshot does not contain: the **bid** side of the book, and a price
time series.

That is a data request, not a model idea, and it is the honest next step
rather than re-optimizing this dataset until it turns positive.

`mirror_live = false`. No capital, no orders, no production writes.
