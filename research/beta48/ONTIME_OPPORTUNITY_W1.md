# How many on-time opportunities actually existed

`research/bettor_ontime_opportunity.sql`, research-sql run 176 on
`ecb0928`, 2026-09-20T22:39:05Z. W1 cohort, 66 observations. Read only,
no settlement term.

## The measurement

For every cohort observation and every horizon: did a tick actually
occur inside the selectable window `[T0+h, T0+h+30]`?

| horizon | band elapsed | had a tick in band | ...and that tick had follow-up budget |
|--------:|-------------:|-------------------:|--------------------------------------:|
|      60 |           66 |        66 (100%)   |                                     31 |
|     300 |           66 |         2 (3.0%)   |                                      1 |
|     900 |           66 |         9 (13.6%)  |                                      8 |
|    3600 |           18 |         0 (0%)     |                                      0 |

Tick spacing, measured over 68 intervals: min 21.6s, p50 71.4s, max
82.5s. Median offset of an observation from its own tick: 3.7s.

## Three quantities, deliberately not pooled

- **A tick in band** is a scheduling *chance*.
- **A tick in band with follow-up budget** is a chance the scheduler
  could have taken.
- **An on-time read** is an outcome. W1 recorded zero of these at every
  horizon, against 66 chances at the 60s horizon and 31 funded ones.

The gap between the second and third columns is the allocation loss;
the gap between the first and second is the budget loss. They are
separate defects and separating them is the point of the query.

## My phase estimate was wrong, and wrong in the unhelpful direction

I estimated 30/72 ≈ 42% of observations would have a tick inside the
300s band. Measured: **3%**.

The estimate assumed `T0` is uniformly distributed against tick
boundaries. It is not. **The same tick loop that writes an observation
runs the follow-up pass**, so `T0` sits a median 3.7s after its own
tick and every later tick is ~71.4s further on. Achievable lags are
therefore *quantised* to `k·71.4 − 3.7`, and a horizon is reachable
only if some integer `k` lands inside its window:

| horizon | `k` must lie in | integer? |
|--------:|:----------------|:---------|
|      60 | [0.89, 1.31]    | k=1 ✓ |
|     300 | [4.25, 4.67]    | none — **unreachable** |
|     900 | [12.66, 13.08]  | k=13 ✓ |
|    3600 | [50.47, 50.89]  | none — **unreachable** |

That reproduces the measured 100% / 3% / 13.6% / 0% exactly. The 3% at
300s and the 13.6% at 900s are tick-spacing *jitter*, not phase luck.
The 30/72 figure is withdrawn.

## What follows for W3

Opening eligibility at `T0+h−30` widens the selectable window to the
full tolerance band and changes the 300s requirement to `k ∈ [3.83,
4.67]`, which contains **k=4**. That is the entire mechanism of the
eligibility change, and it is why the change is necessary rather than
merely tidy.

It does **not** fix 3600s: `k ∈ [50.05, 50.89]` still contains no
integer at the median spacing. That horizon depends on jitter, and no
allocation rule can supply it.

The structural fix would be to **decouple `T0` from tick boundaries**
so lags stop being quantised at all. That is a separate change to the
frozen sampling rule, it is not taken here, and it must not be made
silently.

## What this is not

This measures scheduling opportunity. It says nothing about markets,
fills, profitability or adverse selection, and it carries no settlement
term. Objects A, B, C and D remain separate; B, C and D remain
NOT_IDENTIFIED.
