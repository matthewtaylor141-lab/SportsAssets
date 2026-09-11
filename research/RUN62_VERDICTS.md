# Run 62 — the four selection verdicts

Read from run 62's log (2026-09-11, 11m02s, `psql exit=0`). Window throughout:
canonical RN1 fills, `2026-08-05 00:00Z ≤ ts < 2026-09-11 12:00Z`.
26,248 conditions, acquisition cost $74,936,114.

**Terminology lock applied.** What the SQL column calls `residual_roi_pct` is
reported here as **`NON_MATCHED_REMAINDER`**. It is `TRADING_PNL −
MATCHED_GROSS_PNL`, a subtraction remainder, and it is **not** yet shown to be
directional residual economics — it can also contain realized SELL effects on
inventory already counted in M, merge/redemption cash flows, explicit fees, and
reconciliation artifacts. The word *directional* is reserved for the cleaner
follow-up.

---

## AUDIT 1 — the PMUS mapper bridge

Full canonical population.

| | BRIDGED | UNBRIDGED |
|---|---|---|
| conditions | 2,565 | 23,683 |
| fills | 52,220 | 315,588 |
| acquisition cost | $14,404,722 | $60,531,392 |
| matched cost | $9,828,983 | $37,144,703 |
| matched quantity | 10,492,139 | 46,313,773 |
| matched share of acq cost | **68.23%** | **61.36%** |
| p50 pair cost | 0.9843 | 0.9810 |
| **gross matched edge (% of matched cost)** | **0.804%** | **1.568%** |
| p50 / p90 fill size | 54.5 / 2,945.4 | 43.9 / 1,660.1 |
| p50 price | 0.4800 | 0.4650 |
| acq under 25c / over 75c | 4.00% / 28.97% | 5.11% / 33.88% |
| venue lane *(OUR INGESTION PROPERTY)* | 18.22% | 65.29% |
| market_slug present | 2,565 (100%) | 20,122 (85.0%) |

Realized, **same-settlement-subset denominators only**:

| | BRIDGED | UNBRIDGED |
|---|---|---|
| conditions settled | 1,838 / 2,565 = **71.66%** | 12,460 / 23,683 = **52.61%** |
| acq cost settled | $9,596,864 | $32,313,062 |
| matched cost settled | $6,421,626 | $19,828,396 |
| matched ROI | 1.905% | 1.944% |
| **NON_MATCHED_REMAINDER ROI** | **−1.228%** | **+1.234%** |
| total ROI | 0.756% | 1.567% |

## AUDIT 2 — settlement retention

| class | conditions | % | acq cost | p50 fills | p50 / p90 fill size | venue lane | bridged | latest last fill |
|---|---|---|---|---|---|---|---|---|
| 1 SETTLED_RETAINED | 14,298 | 54.47 | $41,909,926 | 4.0 | 42.0 / 1,751.7 | 60.37% | 12.85% | 2026-09-11 11:59 |
| 2 NOT_YET_RESOLVED | 8,389 | 31.96 | $25,633,234 | 3.0 | 61.0 / 1,787.6 | 34.39% | 8.67% | 2026-09-11 11:46 |
| 3 RESOLVED_BUT_SETTLEMENT_MISSING | **0** | 0.00 | — | — | — | — | — | — |
| 4 NO MARKETS ROW AT ALL | 3,561 | 13.57 | $7,392,953 | 4.0 | 31.1 / 1,554.0 | **100.00%** | 0.00% | 2026-09-05 16:04 |

**Class 3 is empty.** There is no "resolved but payout dropped" retention bug.
Missingness is entirely *not yet resolved* or *no metadata row*.

Retention by calendar week of last fill:

| week | conditions | acq cost | settled | not yet resolved | resolved no payout | no markets row |
|---|---|---|---|---|---|---|
| 2026-08-03 | 4,297 | $11,264,631 | 65.72% | 0.58% | 0.00% | **33.70%** |
| 2026-08-10 | 3,855 | $11,806,138 | 60.96% | 4.31% | 0.00% | **34.73%** |
| 2026-08-17 | 2,057 | $6,605,517 | 59.36% | 5.15% | 0.00% | **35.49%** |
| 2026-08-24 | 6,660 | $17,318,395 | 41.62% | **58.33%** | 0.00% | 0.05% |
| 2026-08-31 | 6,311 | $15,489,302 | 39.01% | **60.34%** | 0.00% | 0.65% |
| 2026-09-07 | 3,068 | $12,452,131 | **86.99%** | 13.01% | 0.00% | 0.00% |

**This is not simple right-censoring.** Under censoring, retention would fall
monotonically toward the present. It does the opposite: the most recent week is
the *best* covered (86.99%) while 08-24 and 08-31 are the worst (41.62%,
39.01%) with ~59% not yet resolved. Two distinct mechanisms overlap — a
metadata-ingestion gap concentrated in Aug 3–17 (~34% with no markets row,
ending after 2026-09-05) and a cohort of genuinely long-dated markets
concentrated in Aug 24–31.

## Sport composition (share of total acquisition cost)

Tennis **46.96%** (34.98 unbridged + 11.98 bridged), Soccer **25.51%**
(22.10 + 3.41), no-markets-row 9.87%, MLB 6.99%, Non-Sports 4.18%,
Other-Sports 3.68%, NFL 2.29%, NBA 0.29%, MMA 0.22%.

Within-group: bridged is **62.3% Tennis**; unbridged is **43.3% Tennis**.

---

# THE FOUR VERDICTS

### 1. `PMUS_BRIDGE_REPRESENTATIVE?` → **NOT SUPPORTED**

Every measured dimension differs, and the economic one differs most: **gross
matched edge is 0.804% bridged against 1.568% unbridged — the bridged
population carries barely half the matched edge per dollar of matched cost.**
Alongside that: matched share 68.23% vs 61.36%; p90 fill size 2,945 vs 1,660
(1.77×); settlement rate 71.66% vs 52.61%; Tennis 62.3% vs 43.3% of within-group
cost; detection lane 18.22% vs 65.29% venue *(our ingestion property, reported
as evidence of selection, never as an explanation of RN1's economics)*. The
bridge reaches 9.77% of conditions and 19.22% of acquisition cost.

### 2. `SETTLED_SUBSET_REPRESENTATIVE?` → **NOT SUPPORTED**

54.47% of conditions and 55.93% of deployed, and the missingness is
**structured, not random**. Retention moves 65.72% → 39.01% → 86.99% across
consecutive weeks — non-monotonic, so not censoring. Two identified mechanisms:
a metadata gap in Aug 3–17 (~34% no markets row, 100% venue-lane detected, 0%
bridged, nothing after 2026-09-05) and long-dated markets in Aug 24–31 (~59%
unresolved). Pre-settlement variables also differ: p50 fill size 42.0 settled
vs 61.0 not-yet-resolved vs 31.1 no-markets-row.

### 3. `MATCHED_MECHANISM_GENERALIZABLE?` → **SUPPORTED** (updated by run 63)

The payoff invariant holds overwhelmingly **where it can be observed**:
101,734 of 101,757 retained settled payout vectors sum to 1 (**99.9774%**), 23
do not (0.0226%). *Scope note: that test ran over all retained settled markets,
not only RN1's conditions.*

But per instruction this may **not** be extended to the unresolved population
by inference from the settled subset. The structural eligibility check — exactly
two complementary outcome tokens, no condition with more than two outcome
slots, no malformed pairing, no contract type where YES+NO is not a $1
complementary pair — **has not been run**. Until it is, matched gross economics
on unresolved conditions are **structurally assumed, not empirically verified**,
and the verdict stays INDETERMINATE.

**UPDATE — run 63 resolves this.** The structural check is done, without
settlement.

| eligibility | conditions | % | acquisition cost | % | matched cost | % |
|---|---|---|---|---|---|---|
| STRUCTURALLY_ELIGIBLE (exactly two slots, index 0 and 1) | 22,686 | **86.429** | $67,542,815 | **90.134** | $42,578,503 | **90.643** |
| STRUCTURALLY_INELIGIBLE | **0** | 0.000 | — | — | — | — |
| STRUCTURE_UNKNOWN (no catalogue rows) | 3,562 | 13.571 | $7,393,299 | 9.866 | $4,395,183 | 9.357 |

**Not one malformed condition.** Every RN1 condition that has catalogue rows is
a clean two-slot `{0,1}` pair — no >2-outcome conditions, no duplicate or
missing index, no null slot. And on RN1's own conditions specifically,
**14,305 of 14,305 retained payout vectors sum to exactly 1 (100%)**, with zero
exceptions and zero non-2-element arrays. (Run 62's 23 exceptions were in other
markets, not RN1's.)

So matched gross economics are legitimate on **90.134% of acquisition cost**
without any settlement — against the 55.93% the settled subset allows. The
remaining 9.866% is `STRUCTURE_UNKNOWN` and stays **excluded, not assumed**.

Matched mechanism on STRUCTURALLY_ELIGIBLE only, settlement-free:

| | conditions | acq cost | matched cost | matched qty | matched % of acq | p50 pair cost | matched gross P&L | **matched gross ROI** |
|---|---|---|---|---|---|---|---|---|
| BRIDGED | 2,565 | $14,404,722 | $9,828,983 | 10,492,139 | 68.23 | 0.9843 | $79,034 | **0.804%** |
| UNBRIDGED | 20,121 | $53,138,093 | $32,749,520 | 40,621,675 | 61.63 | 0.9811 | $509,743 | **1.556%** |
| **ALL ELIGIBLE** | **22,686** | **$67,542,815** | **$42,578,503** | **51,113,813** | **63.04** | **0.9817** | **$588,777** | **1.383%** |

### A consequence for verdict 2, now quantified

Matched ROI on the **settled subset** (run 62) against the **full eligible
population** (run 63), same formula:

| | settled subset | all eligible | overstatement |
|---|---|---|---|
| BRIDGED | 1.905% | 0.804% | **2.37×** |
| UNBRIDGED | 1.944% | 1.556% | 1.25× |

**The settled subset overstates the matched mechanism**, and on the bridged
population it more than doubles it. That is verdict 2's non-representativeness
expressed directly in the economics rather than in covariates.

### 4. `NON_MATCHED_REALIZED_MECHANISM_GENERALIZABLE?` → **NOT SUPPORTED**

Three independent reasons. (a) It is computable only on the settled subset,
which verdict 2 finds non-representative. (b) The remainder is contaminated and
the contamination is not yet quantified — SELL effects on inventory already
counted in M, merges/redemptions, fee treatment. (c) Most tellingly, **it flips
sign across a selection dimension**: −1.228% bridged against +1.234%
unbridged, on the same definition and the same settlement basis. A quantity
that changes sign with mapper coverage cannot be generalized from either half.

---

No causal language is drawn from accounting-derived (class A) variables
anywhere above. No repair of `game_start` coverage was attempted.
`mirror_live=false`.
