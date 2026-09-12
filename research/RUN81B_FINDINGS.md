# RUN 81B — THE EVENT-LEVEL REPRICING COUNTERFACTUAL

Run: `python3 research/gen/run81b.py` — sealed bytes only, no database.
Independent check: `python3 research/gen/validate_81b.py`.
Gate: `python3 research/gen/run81b_population_gate.py`.

`mirror_live=false`. `ai_trades` / TRUEEDGE untouched.

---

## THE ONE QUESTION 81B ANSWERS

> For the exact settlement-analyzable U2 BUY events, what realized settlement
> P&L corresponds to RN1's source fill price, and what would that **same
> quantity's** realized settlement P&L have been if entry occurred instead at
> the first retained observable ask?

It is **not** a reconstruction of RN1's strategy, not a matched-book
reconstruction, not expected value, not alpha, not arbitrage edge.

---

## THE HEADLINE — Q_A (PRIMARY, 10% OF RN1's SOURCE FILL SHARES)

| | |
|---|---|
| events / conditions | 112,543 / 9,336 |
| scenario source acquisition cost | **$2,472,713.31** |
| `SETTLEMENT_REALIZED_SOURCE_COUNTERFACTUAL_PNL` | **+$45,437.46**  (ROI +1.84%) |
| `FIRST_RETAINED_OBSERVATION_COUNTERFACTUAL_PNL` | **−$42,856.87**  (ROI −1.67% on its own $2,561,007.64 cost) |
| `TOP_OF_BOOK_PNL_DETERIORATION` | **$88,294.33** — 3.571% of source cost |
| events deteriorating | 100,833 of 112,543 (89.595%) |

**The sign flips.** The deterioration is not a haircut on the margin; at
$88,294 against $45,437 it is **1.94×** the entire source-price result.

### Depth-aware, on the 112,381 supported events (99.856%)

| | |
|---|---|
| `SOURCE_DEPTH_SUBSET_COUNTERFACTUAL_PNL` | +$48,478.63 |
| `FIRST_OBSERVED_DEPTH_COUNTERFACTUAL_PNL` | **−$62,873.80** |
| `TOP_COMPONENT` | $85,402.17 — **76.695%** |
| `DEPTH_COMPONENT` | $25,950.26 — **23.305%** |
| `TOTAL_OBSERVED_PNL_DETERIORATION` | $111,352.42 — 4.549% of supported source cost |

162 events are `DEPTH_EXHAUSTED`: `FULL_Q_EXECUTION_PNL = NOT IDENTIFIED`. Not a
bound in either direction, not extrapolated, not folded into any aggregate.

## Q_B (SECONDARY — HIS FULL SHARES)

Top of book scales exactly ten-fold, because `q` does: source +$454,374.60,
first-observed −$428,568.74, deterioration $882,943.34, same 3.571% of cost.

Depth does **not** scale. Support falls to 98.707% (1,455 exhausted), and on the
supported rows total deterioration is **$1,228,840.29** split **56.54% top /
43.46% depth** — the depth share nearly doubles against Q_A.

## Q_C (STRESS ONLY — 1000 / p_h shares; NOT realistic BETTOR sizing)

Support falls to 82.393% (19,815 exhausted) and depth becomes the **majority**
of deterioration (57.20%). Reported for the shape of the curve. It drives no
conclusion.

---

## WIN / LOSS SPLIT AT Q_A — DESCRIPTIVE ONLY

| S | events | source cost | source CF P&L | first-obs CF P&L | deterioration |
|---|---|---|---|---|---|
| 1 | 54,657 | $1,563,493.87 | +$954,656.90 | +$910,987.97 | $43,668.93 |
| 0 | 57,886 | $909,219.44 | −$909,219.44 | −$953,844.84 | $44,625.40 |
| ALL | 112,543 | $2,472,713.31 | +$45,437.46 | −$42,856.87 | $88,294.33 |

Deterioration is `q·(best_ask − p_h)` and **does not depend on S at all**. The
near-equal split is a statement about where the events landed, not evidence that
deterioration behaves differently on winners and losers. The mechanism it shows
is arithmetic: a worse entry price shrinks the winners *and* deepens the losers,
so it hits a book from both sides at once.

---

## SEGMENTATION AT Q_A — DESCRIPTIVE, NEVER CAUSAL

Segments differ in composition as well as execution. Nothing below identifies a
cause.

**By sport** — two sports are 73% of the cohort's cost and behave differently at
source: Tennis 40,839 events / $996,678.82 cost / **+0.2%** source ROI, Soccer
41,403 / $883,803.07 / **+3.2%**. After repricing both are negative (−2.5% and
−0.4%). MLB is the one sport that survives repricing positive (+1.4% on $85,393).

**By source price band** — the extremes carry the damage. `[0.00,0.05)`: 5,419
events, $5,844.37 cost, source ROI **+39.7%**, deterioration **$11,822.77 —
202% of the cost itself** — first-observed ROI **−53.8%**. A cheap longshot's
edge lives entirely in the cents, and the cents are what moved.

**By RN1 fill-size band** — one segment survives. `[10,000,100,000)` shares: 387
events, $271,511.33 cost, source P&L +$30,396.79 (**+11.2%**), first-observed
**+$20,722.65 (+7.4%)**. It is the only size band whose repriced economics stay
positive, and it is 387 events. Named because it is the one place the cohort
does not invert — not because 387 events establish anything.

**By source lane** — chain 101,312 / poll 10,139 / s1 1,092. Composition only:
81B uses no clock anywhere, so the lane split is not a latency reading.

---

## THE SEVEN ANSWERS

1. **Realized settlement counterfactual P&L at his source fill prices:**
   +$45,437.46 at Q_A; +$454,374.60 at Q_B.
2. **Same events at the first retained top-of-book ask, Q_A:** −$42,856.87.
3. **After retained depth walking, Q_A supported events:** −$62,873.80 (on the
   112,381 rows whose source-price result is +$48,478.63; the other 162 are
   `NOT IDENTIFIED`).
4. **Deterioration at Q_A:** $88,294.33 before depth on all valid events;
   on the supported subset $85,402.17 top + $25,950.26 depth = $111,352.42.
   The first figure and the rest are different populations and are not summed.
5. **Split:** top-of-book **76.695%**, depth **23.305%**.
6. **The narrow hypothesis — "by the first retained observation, the economics
   available to a reactive copier are materially worse than RN1's source-fill
   economics" — is SUPPORTED on this selected cohort.** Decision rule, stated
   in the code and applied as written: deterioration > 0, removing ≥ 10% of the
   source-price counterfactual P&L, with > 50% of events deteriorating. Measured:
   $111,352.42, **229.694%**, **89.595%**. The rule is mine, not owner-approved,
   and it was not blind — 81A had already established the direction. Every raw
   number is printed so a different rule can be applied to the same figures.
   **This does not establish that physical latency caused the deterioration.**
7. **What remains unidentified** — fees and rebates; the clock
   (`TIMESTAMP_INTEGRITY` unresolved, and "first retained observation" is a
   property of probe cadence, not elapsed time); what a copier would actually
   have been *filled* at (no queue position, partial fills, cancellation, or own
   market impact are modelled); the unselected 47.6% of U2 events; RN1's complete
   book (67.248% U0 coverage, 100% BUY); external payout correctness; retained
   depth beyond what was stored.

---

## MATCHED REGISTER — NOT COMPUTED

```
FULL_RN1_MATCHED_BOOK_RECONSTRUCTION = NOT IDENTIFIABLE FROM CURRENT SEALED INPUTS
```

U2 contains 112,543 of 167,354 U0 events on these conditions (67.248%), and the
sealed U0 timing witness carries no price and no size. Complete condition-level
acquisition pools therefore cannot be reconstructed from the current sealed
inputs. The 4,770 fully-covered conditions are a **coverage diagnostic** and are
not a cohort. No new U0 price/size artifact was drawn.

## PERMANENT LIMITATIONS

- `FEE_AND_REBATE_ADJUSTED_NET_ECONOMICS = NOT IDENTIFIED`. Every figure is
  **BEFORE UNIDENTIFIED FEES / REBATES / REWARDS**. Gross is not called an upper
  bound and not called a lower bound. No historical fee formula is imported.
- **Payout limitation.** The sealed vectors are structurally well-formed and map
  unambiguously on this cohort. Their independent external correctness has not
  been established.
- **Depth limitation.** `DEPTH_EXHAUSTED` ⇒ `FULL_Q_EXECUTION_PNL = NOT
  IDENTIFIED`. No extrapolation, no imputation, not a bound.
- **Selection.** *This population is settlement-selected. It represents the
  subset of `U2_SNAPSHOT_V1` that was linkable, structurally eligible, resolved
  in the sealed settlement metadata state, passed the stronger timing
  quarantine, and had unambiguous payout mapping. It is not established as
  representative of all U2 events or all RN1 activity.*

---

## GATES AND VERIFICATION

| gate | result |
|---|---|
| sealed hash identity (3 artifacts, canonical scope) | MATCH |
| population controls | 112,543 / 9,336 / $24,727,133.10 — MATCH |
| payout unambiguity | 2 distinct S values, both exactly 0 or 1; 0 ambiguous |
| eligibility | 112,543 of 112,543 eligible; **zero exclusions** |
| Bridge 1 closure, per event | 0 violations / 112,543 witnesses, max residual 2.9e-13 |
| Bridge 2 closure + decomposition | 0 / 0 violations, max residual 7.3e-12 |
| `EVENT_IDENTITY_DIGEST` | `0c258f787af42c50be9b0501d21a52f2a89d27d013026d24eee79b77bac5814d` |

**Independent validation.** `validate_81b.py` re-derives the population from raw
sealed JSON (not imported), computes in 50-digit `Decimal` instead of IEEE
double, walks depth by prefix-sum and bisect instead of accumulation, and
**never forms a VWAP** — it works from the walk cost directly, so
`TOTAL = cost − q·p_h`, `DEPTH = cost − q·ask`, `TOP = q·ask − q·p_h`. Every
reported figure agrees **to the cent**, and the identity digest matches, so the
agreement is about the same rows and not a coincidence of totals. The only
non-zero decomposition residual anywhere is Q_C's 1e-46, which is `1000/p_h`
being inexact at 50 digits.

Zero violations are reported only beside non-zero witness counts. A zero-witness
cell reads `NOT TESTED`, never `PASS`.
