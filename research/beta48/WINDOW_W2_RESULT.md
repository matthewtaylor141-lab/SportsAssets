# W2 — the follow-up budget reservation, measured

Window **2026-09-20T22:18:17Z → 22:48:17Z**, declared at 22:10Z before
the repair deployed. Bounds fixed by the rule (deploy `7101382` live
22:08:17Z + 10 min warm-up), not chosen after seeing rows.

**Provenance.** 24 ticks, one pacing version
(`BETTOR_CAPTURE_PACING_V2_ADAPTIVE`), one universe version
(`BETTOR_UNSELECTED_STATE_V2`). `7101382` was the only live deploy for
the whole window — verified from the deploy record, not inferred.
Query: `research/bettor_window_w2.sql`, research-sql run 177.

## Initial-read coverage — the price of the reservation

| | W1 | W2 |
|---|---:|---:|
| scheduled | 148 | 116 |
| attempted | 66 | 41 |
| skipped for budget | 82 | 75 |
| **coverage** | **44.6%** | **35.3%** |

Coverage **fell 9.3 points**. That was expected and predeclared as the
cost of reserving budget for follow-ups; it is reported, not hidden.
Of the 41 attempts: 22 readable, 5 rate-limited, 14 unreadable-other.

## Per horizon, cohort-fixed (41 observations)

| horizon | not yet due | eligible | **attempts** | **on time** | late | pending | finally missing |
|--------:|------------:|---------:|-------------:|------------:|-----:|--------:|----------------:|
|      60 |           0 |       41 |           41 |      **16** |   25 |       0 |               0 |
|     300 |           7 |       34 |           10 |       **0** |   10 |       9 |              15 |
|     900 |          19 |       22 |        **0** |       **0** |    0 |      15 |               7 |
|    3600 |          41 |        0 |            0 |           0 |    0 |       0 |               0 |

Every row sums to the cohort of 41. Nothing is dropped, and nothing
pending is counted as missing.

Tick ledger: FU_DUE (true outstanding demand) **190**, FU_SELECTED 64,
FU_ATTEMPTED 64, FU_SKIPPED_BUDGET 0, FU_ON_TIME 13, FU_LATE 51. The 64
tick-level attempts exceed the 51 cohort attempts because 13 of them
belong to observations made before the window — different populations,
counted apart.

## Against the criteria as declared

| criterion | result |
|---|---|
| `ON_TIME_60S` — > 0% | **PASS**, 16/41 = 39.0% |
| `ON_TIME_300S` — ≥ 50% | **FAIL**, 0/34 = 0% |
| `INITIAL_COVERAGE_COST` — reported | reported: 35.3% vs 44.6% |
| `FAIL_IF` — on-time zero at every horizon | **not triggered** |

**Diagnosis and repair effectiveness, reported separately.**

- The **diagnosis was right**. W1 returned zero on-time reads at every
  horizon; W2 returns 16. Follow-ups were starved of budget, and
  funding them produced on-time reads where there had been none.
- The **repair is partial**. It fixed the 60s horizon and left 300s and
  900s at zero. It was one repair to one defect, and two more remain.

### A defect in my own W2 declaration

`PASS_IF.ON_TIME_300S` justified its 50% bar with "the 300s window is
[270,330]s — 60s wide against a 60s tick". **That window did not exist
in the code W2 ran.** Under the V2 eligibility rule the scheduler could
not select an observation until T0+300 while ON_TIME closes at T0+330,
so the selectable window was [300,330] — 30s wide, against ticks 65–82s
apart. The criterion described a sampling rule that had not shipped.

W2 is reported against the bar **as declared**, and fails it. The bar
is not restated now that the number is in.

## The two remaining defects, both measured in production

**1. Horizon starvation — allocation, not capacity.** 900s received
**zero attempts against 22 eligible observations**. FU_SKIPPED_BUDGET
was 0 and 64 follow-ups were attempted: the budget was spent, just
never on 900s. The loop walks horizons in a fixed order, so 60s took 41
and 300s took 10 and the budget was gone before 900s was reached.

**2. Eligibility — timing, not allocation.** 300s attempted 10 reads
and landed **none** on time, at p50 lag 735s against a 300s horizon.
Selection opened at exactly T0+h while the tolerance closes at T0+h+30.
`research/bettor_ontime_opportunity.sql` (run 176) measured the
consequence on the W1 cohort: only **2 of 66** observations ever had a
tick inside the 300s band.

Both are addressed by W3 (`0868e3e`, live 22:53:17Z). Neither is
addressed by widening the tolerance, which remains 30s with ON_TIME the
only gate-admissible class.

## What W2 does not establish

W2 measures one repair to the collection system. **The collection
system is not complete.** Demand still exceeds capacity — FU_DUE 190
against 64 attempted, roughly 3× — so reads will continue to be lost;
the question W3 addresses is only *which* ones.

Nothing here is evidence about markets, fills, profitability or adverse
selection. No settlement term appears in any of these queries. Objects
A, B, C and D stay separate and B, C and D remain NOT_IDENTIFIED.
`mirror_live=false`, real-order activity NONE, real capital at risk 0.
