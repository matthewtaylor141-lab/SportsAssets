# FOUR_WHALE_INTELLIGENCE_AUDIT

Distillation of the four deeply studied accounts into the BETTOR design
inventory. **Not** a new whale search. Every figure below is recomputed from
the retained hash-verified reconstructions of run `35034361586`.

```
ACCOUNTS         rn1, ferrarichampions2026, swisstony, homerunhazard
EVIDENCE         blobs_v3 reconstructions, LIFETIME window
GRANULARITY      AGGREGATE (census, windowed replay, completion grid,
                 per-band channel decomposition)
PER_POSITION_ROWS            NOT PRESENT
HISTORICAL_BOOK_STATE        NOT PRESENT
```

---

## 0. THE FEASIBILITY BOUNDARY, STATED FIRST

The transported payloads are 44–87 KB of **aggregates**. RN1 alone has 4.7M
fills; they are not in here and were never transported. Consequently:

| asked for | status |
|---|---|
| per-band channel P&L | **AVAILABLE** |
| completion hazard by time × basis ceiling | **AVAILABLE** |
| completion by price band / sport / size / week | **AVAILABLE** |
| per-position lifecycle rows | **NOT CONSTRUCTIBLE** |
| `*_THROUGH_TIME`, MFE, MAE | **NOT CONSTRUCTIBLE** — no book states |
| executable passive / aggressive exit prices | **NOT CONSTRUCTIBLE** |
| per-position residual-loss hazard | band-level only |

No historical book state is manufactured anywhere in this document.

---

## 1. MECHANISM MAPS

Fields with no supporting evidence are `NOT_IDENTIFIED` rather than inferred.

### RN1

```
ENTRY_LOGIC_OBSERVED         NOT_IDENTIFIED. Entry PRICE BAND distribution is
                             observable; the decision rule is not.
MAKER_BEHAVIOR               None observed. In the separate Polymarket CLOB
                             probe every row is side=BUY: he takes.
TAKER_BEHAVIOR               Primary. The maker facing him lost 0.0090/share
                             (different venue, 2026-08-06..09-11).
PAIR_COMPLETION_BEHAVIOR     Strong. MERGE_PNL +$10,359,105.
COMPLEMENT_TIMING            F(5s)=4.98%, F(60s)=16.83%, F(1h)=67.41%,
                             F(settlement)=76.51%. Fastest of the four.
PAIR_BASIS_BEHAVIOR          See the frontier in the appendix.
RESIDUAL_INVENTORY_BEHAVIOR  23.49% of first legs never complete.
HOLD_DURATION                t25=120s, t50=600s, t75=1800s (of EVENTUAL
                             completions).
EXIT_BEHAVIOR                234 sells, $583,580 stake = 0.153% of activity,
                             SELL_PNL +$370,281 (ROI +63.4%).
SETTLEMENT_BEHAVIOR          SETTLED_PNL +$2,968,645 -- POSITIVE.
CAPITAL_RECYCLING_BEHAVIOR   Via merge/redeem. Sale is negligible.
POSITION_SIZING              BY_FILL_SIZE_BUCKET available; rule NOT_IDENTIFIED.
PRICE_BAND_BEHAVIOR          Pair engine strong 0.00-0.50, negative 0.70+.
SPORT / MARKET_TYPE          BY_SPORT_OR_QUESTION, top-25 only. PARTIAL.
KNOWN_PROFIT_SOURCE          Pair channel AND settled channel both positive.
                             The only account of the four where that is true.
KNOWN_LOSS_SOURCE            0.70-1.01 merges lose; settled more than offsets.
UNRESOLVED_BEHAVIOR          RN1_TWO_SOURCE_COVERAGE_DISCREPANCY -- two
                             measurements of the same band split differ by
                             2.1-21.2 pp in EVERY band. Open.
```

### ferrarichampions2026

```
ENTRY_LOGIC_OBSERVED         NOT_IDENTIFIED.
MAKER_BEHAVIOR               NOT_IDENTIFIED.
TAKER_BEHAVIOR               NOT_IDENTIFIED.
PAIR_COMPLETION_BEHAVIOR     Strongest gross engine: MERGE_PNL +$10,794,896.
COMPLEMENT_TIMING            F(5s)=2.89%, F(120s)=16.04%, F(1h)=61.00%,
                             F(settlement)=68.31%. SLOWEST eventual completion.
PAIR_BASIS_BEHAVIOR          Mean pair basis 0.938 in the cheapest band.
RESIDUAL_INVENTORY_BEHAVIOR  31.69% never complete -- the worst of the four.
HOLD_DURATION                t25=300s, t50=600s, t75=1800s.
EXIT_BEHAVIOR                18 sells, 0.161% of stake, SELL_PNL +$601,892.
SETTLEMENT_BEHAVIOR          SETTLED_PNL -$8,461,469. THE LEAKAGE.
CAPITAL_RECYCLING_BEHAVIOR   Merge-driven.
POSITION_SIZING              PARTIAL.
PRICE_BAND_BEHAVIOR          See appendix; 0.10-0.30 is net NEGATIVE.
SPORT / MARKET_TYPE          PARTIAL.
KNOWN_PROFIT_SOURCE          The pair channel, unambiguously.
KNOWN_LOSS_SOURCE            The settled channel gives back 78.4% of it.
UNRESOLVED_BEHAVIOR          Whether the settled losses originated as attempted
                             pair inventory. PROVEN_FAILED_PAIR_RESIDUAL_PNL =
                             NOT_IDENTIFIED.
```

### swisstony

```
STATUS                       FLAGGED_EXCLUDED from clean ground truth on an
                             unresolved two-source discrepancy. Every line
                             below inherits that exclusion.
PAIR_COMPLETION_BEHAVIOR     MERGE_PNL +$260,125 on the LARGEST activity base
                             of the cohort ($778.8M stake).
COMPLEMENT_TIMING            F(1h)=46.98%, F(settlement)=75.75%.
RESIDUAL_INVENTORY_BEHAVIOR  24.25% never complete.
HOLD_DURATION                t25=300s, t50=1800s, t75=settlement. SLOW.
EXIT_BEHAVIOR                ZERO sells in every band.
SETTLEMENT_BEHAVIOR          SETTLED_PNL +$23,357,746 -- 98.9% of its total.
CAPITAL_RECYCLING_BEHAVIOR   NOT_IDENTIFIED.
KNOWN_PROFIT_SOURCE          The SETTLED channel, overwhelmingly. This is a
                             directional book, not a pair book, whatever its
                             merge count says.
KNOWN_LOSS_SOURCE            Merges negative in 0.50-1.01.
UNRESOLVED_BEHAVIOR          The exclusion itself.
```

### homerunhazard

```
PAIR_COMPLETION_BEHAVIOR     MERGE_PNL -$476,915. NEGATIVE.
COMPLEMENT_TIMING            F(1h)=53.83%, F(settlement)=75.90%.
RESIDUAL_INVENTORY_BEHAVIOR  24.10% never complete.
HOLD_DURATION                t25=300s, t50=1800s, t75=settlement.
EXIT_BEHAVIOR                ZERO sells. The purest hold-to-settlement book.
SETTLEMENT_BEHAVIOR          SETTLED_PNL +$2,971,302 -- ALL of its profit.
CAPITAL_RECYCLING_BEHAVIOR   None observable.
KNOWN_PROFIT_SOURCE          Settlement alone.
KNOWN_LOSS_SOURCE            The pair channel itself.
UNRESOLVED_BEHAVIOR          Why it pairs at all, given the pair channel loses.
```

---

## 2. THE BAND × CHANNEL MATRIX — AND A REGULARITY IN ALL FOUR

```
CELL LEGEND   PAIR_STRONG / SETTLED_WEAK   merge > 0, settled < 0
              PAIR_WEAK / SETTLED_STRONG   merge < 0, settled > 0
              BOTH_STRONG                  both > 0
              LOW_MERGE_BASE_UNSTABLE      |merge| < 2% of the account's total
                                           merge P&L -- ratios suppressed
```

| band | rn1 | ferrari | swisstony | hrh |
|---|---|---|---|---|
| 0.00–0.10 | PAIR_STRONG / SETTLED_WEAK | PAIR_STRONG / SETTLED_WEAK | PAIR_STRONG / SETTLED_WEAK | BOTH_STRONG |
| 0.10–0.30 | PAIR_STRONG / SETTLED_WEAK | **PAIR_STRONG / SETTLED_WEAK** | BOTH_STRONG | PAIR_STRONG / SETTLED_WEAK |
| 0.30–0.50 | PAIR_STRONG / SETTLED_WEAK | PAIR_STRONG / SETTLED_WEAK | BOTH_STRONG | BOTH_STRONG |
| 0.50–0.70 | BOTH_STRONG | PAIR_STRONG / SETTLED_WEAK | PAIR_WEAK / SETTLED_STRONG | PAIR_WEAK / SETTLED_STRONG |
| 0.70–0.90 | PAIR_WEAK / SETTLED_STRONG | PAIR_WEAK / SETTLED_STRONG | PAIR_WEAK / SETTLED_STRONG | PAIR_WEAK / SETTLED_STRONG |
| 0.90–1.01 | *unstable* | *unstable* | PAIR_WEAK / SETTLED_STRONG | PAIR_WEAK / SETTLED_STRONG |

**The regularity, DESCRIPTIVE not causal:** in every one of the four accounts
the merge channel is positive in the cheap bands and turns negative by
0.70–0.90, while the settled channel does the reverse. **Four independent
implementations, same sign flip, same place.** That is a fact about the venue's
price structure, not about any account's cleverness — and it is the single most
transferable observation in the cohort.

**It is not a trading rule.** It says where each channel historically earned,
not that BETTOR should enter cheap and hold expensive.

---

## 3. THE SELL CHANNEL — HYPOTHESIS GENERATION ONLY

```
SELL_COUNT_COHORT (all six) = 1,248
```

| account | sells | stake share | SELL_PNL | SELL_ROI |
|---|---|---|---|---|
| rn1 | 234 | 0.153% | +$370,281 | +63.4% |
| ferrari | 18 | 0.161% | +$601,892 | +102.8% |
| swisstony | 0 | 0.000% | — | — |
| homerunhazard | 0 | 0.000% | — | — |

**Are sells concentrated where settled economics are worst?** Partially, and
the two accounts that sell disagree:

- **RN1**: 0.10–0.30 is his worst settled band (ROI −14.1%) and takes 15.8% of
  his sells — but his *largest* sell share (34.6%) is 0.30–0.50, where settled
  ROI is only −0.8%. **Not aligned.**
- **Ferrari**: 0.10–0.30 is its worst settled band (ROI −27.5%) and takes 22.2%
  of its 18 sells; but 38.9% sit in 0.50–0.70 where settled ROI is −1.1%.
  **Not aligned.** And 0.00–0.10, at −18.5% settled ROI, received **zero** sells.

```
HYPOTHESIS_GENERATED       Sells are NOT preferentially placed in the bands
                           where holding to settlement performed worst.
OPTIMAL_EXIT_RULE_PROVEN   NO. n=18 and n=234, self-selected, no counterfactual.
```

The honest reading: **these accounts did not use the sell channel as a residual
risk tool.** Whatever the 1,248 sells were for, band-level settled economics do
not explain their placement.

---

# APPENDIX A — FERRARI_CHANNEL_DECOMPOSITION

```
FERRARI_MERGE_PNL    = +10,794,896
FERRARI_SELL_PNL     =    +601,892
FERRARI_SETTLED_PNL  =  -8,461,469
FERRARI_TOTAL_PNL    =  +2,935,319

SETTLED_TO_MERGE_ABS_RATIO = 0.784
PROVEN_FAILED_PAIR_RESIDUAL_PNL = NOT_IDENTIFIED
```

**Label discipline.** The −$8.46M is the **SETTLED_CHANNEL_PNL**. Calling it
`FAILED_PAIR_RESIDUAL_PNL` would assert that those positions originated as
attempted pair inventory, and the evidence does not carry that provenance.
Some may have been deliberate directional holds. The economic contrast stands
regardless: **the pair engine earned $10.79M and the settled channel gave back
78.4% of it.**

| band | merges | MERGE_PNL | M_ROI | settled lots | SETTLED_PNL | S_ROI | TOTAL_PNL |
|---|---|---|---|---|---|---|---|
| 0.00–0.10 | 10,765 | +611,059 | +83.7% | 4,112 | −305,937 | −18.5% | +305,122 |
| 0.10–0.30 | 101,840 | +3,844,029 | +32.3% | 11,142 | **−4,435,219** | −27.5% | **−560,629** |
| 0.30–0.50 | 237,616 | +5,713,028 | +12.0% | 19,439 | −3,462,556 | −5.4% | +2,642,599 |
| 0.50–0.70 | 234,074 | +1,167,177 | +1.9% | 18,195 | −946,843 | −1.1% | +394,579 |
| 0.70–0.90 | 92,402 | −408,535 | −1.4% | 8,069 | +344,266 | +0.9% | −60,037 |
| 0.90–1.01 | 8,034 | −131,863 | −3.3% | 1,655 | +344,820 | +5.9% | +213,686 |

**Where the leakage lives.** 0.10–0.30 and 0.30–0.50 together account for
−$7,897,775 of the −$8,461,469 — **93.3%**. And 0.10–0.30 is the one band where
settlement more than erases a large pair profit: +$3.84M merged becomes
−$560,629 net.

**What this does NOT license.** It does not follow that cutting 0.10–0.30
residuals would have produced +$3.84M. The settled channel there is a
*different set of positions* from the merged ones, and the counterfactual price
at which they could have been exited **does not exist in this evidence**.

---

# APPENDIX B — FERRARI_COMPLETION_HAZARD

Cumulative `F(t)` = P(first leg completed by t), any basis ceiling.

| t | F(t) | survival 1−F(t) | H(interval) | H per minute |
|---|---|---|---|---|
| 5s | 2.89% | 97.11% | 2.89% | 34.72% |
| 10s | 3.99% | 96.01% | 1.13% | 13.54% |
| 30s | 7.01% | 92.99% | 3.14% | 9.43% |
| 60s | 10.58% | 89.42% | 3.84% | 7.68% |
| 120s | 16.04% | 83.96% | 6.11% | 6.11% |
| 300s | 26.87% | 73.13% | 12.89% | 4.30% |
| 600s | 37.13% | 62.87% | 14.04% | 2.81% |
| 1800s | 53.23% | 46.77% | 25.60% | 1.28% |
| 3600s | 61.00% | 39.00% | 16.61% | 0.55% |
| settlement | 68.31% | **31.69%** | 18.75% | *not comparable* |

**THREE SEPARATE CONCEPTS, and only one of them is a hazard rate.**

```
INTERVAL_COMPLETION_H                      [F(t2)-F(t1)] / [1-F(t1)]
    conditional probability of completing SOMEWHERE INSIDE the interval.

AVERAGE_COMPLETION_PROBABILITY_PER_MINUTE  H / interval_minutes
    DESCRIPTIVE ONLY. Not a hazard rate. It is a probability divided by a
    duration: it is not additive across unequal intervals, and a short enough
    interval drives it above 1.0 per minute, which no probability can be.

CONTINUOUS_HAZARD_LAMBDA                   -ln( S(t2)/S(t1) ) / minutes
    the equivalent constant continuous hazard. PREFERRED for comparison
    across unequal intervals, because it is the quantity that IS additive.
```

An earlier version of this appendix labelled `H / minutes` a hazard rate. It is
not, and the correction matters: the two differ at every interval, and only
lambda can be compared between a 5-second bucket and a 30-minute one.

### The corrected lambda series (per minute, any basis)

| interval | rn1 | ferrari | swisstony | hrh |
|---|---|---|---|---|
| 0–5s | 0.6127 | 0.3523 | 0.4408 | 0.3124 |
| 5–10s | 0.2979 | 0.1362 | 0.1923 | 0.2022 |
| 10–30s | 0.1616 | 0.0958 | 0.1085 | 0.0871 |
| 30–60s | 0.1090 | 0.0783 | 0.0669 | 0.0668 |
| 60–120s | 0.0748 | 0.0631 | 0.0420 | 0.0491 |
| 120–300s | 0.0498 | 0.0460 | 0.0274 | 0.0344 |
| 300–600s | 0.0314 | 0.0302 | 0.0174 | 0.0222 |
| 600–1800s | 0.0165 | 0.0148 | 0.0087 | 0.0121 |
| 1800–3600s | 0.0075 | 0.0061 | 0.0042 | 0.0054 |
| 3600s–settlement | *not defined* | *not defined* | *not defined* | *not defined* |

The settlement tail has **no well-defined elapsed duration**, so it has no
lambda and is excluded from every comparison and every monotonicity test.

```
PAIRING_INTENSITY_DECAYS_WITH_TIME = YES
```

Strictly non-increasing in **all four accounts**, over all nine well-defined
intervals, with a first-to-last ratio of **82x (rn1), 58x (ferrari), 105x
(swisstony), 58x (hrh)**. The earlier per-interval reading suggested a *rise*
to a peak at 600–1800s; that was entirely a duration artifact and the properly
normalised series shows monotone decay with no exception anywhere.

The substantive consequence is unchanged and now rests on the right metric: a
leg still unpaired at one hour is in a different population, and the intensity
with which it is still pairing is roughly two orders of magnitude below a fresh
leg's.

### Half-lives, as fractions of EVENTUAL completions

| account | eventual | t25 | t50 | t75 |
|---|---|---|---|---|
| rn1 | 76.51% | 120s | 600s | 1800s |
| ferrari | 68.31% | 300s | 600s | 1800s |
| swisstony | 75.75% | 300s | 1800s | settlement |
| homerunhazard | 75.90% | 300s | 1800s | settlement |

---

# APPENDIX C — FERRARI_BASIS_TIME_FRONTIER

`P(complete by T at pair basis <= B)`, in percent:

| T | ≤0.90 | ≤0.94 | ≤0.96 | ≤0.98 | ≤0.99 | ≤1.00 | any |
|---|---|---|---|---|---|---|---|
| 5s | 0.03 | 0.12 | 0.38 | 1.06 | 1.75 | 2.50 | 2.89 |
| 60s | 0.25 | 0.94 | 2.07 | 4.25 | 6.29 | 8.30 | 10.58 |
| 120s | 0.66 | 1.97 | 3.70 | 6.70 | 9.33 | 11.98 | 16.04 |
| 300s | 2.19 | 4.76 | 7.52 | 11.85 | 15.34 | 18.83 | 26.87 |

**The frontier, read plainly.** At 120 seconds, insisting on a basis of 0.90 or
better yields a **0.66%** completion rate; relaxing to 0.98 yields **6.70%** —
**ten times** the completion probability for 8 points of basis. Relaxing
further to *any* basis yields 16.04%, but everything past 1.00 is a pair
completed at a loss on basis.

```
BETTER BASIS  -> far lower completion probability
WORSE BASIS   -> far higher completion probability
```

**Do not assume the whale-selected basis was optimal.** Ferrari's realised mean
pair basis in the cheapest band was 0.938, which the frontier shows is deep in
the low-completion region. Whether that was a good trade-off depends on the
economics of the completions it forwent — which this archive cannot price.

---

## MACHINE-READABLE PRIORS

The intelligence does not live only in this document.

```
ARTIFACT   research/beta48/evidence/whale_audit/whale_exit_priors_v1.json
BUILDER    research/beta48/build_exit_priors.py     (pure derivation)
TESTS      research/beta48/test_exit_priors.py      (21, all passing)
```

Per account × price band × basis ceiling × time interval it stores
`CUMULATIVE_COMPLETION_F`, `SURVIVAL_S`, `INTERVAL_COMPLETION_H`,
`CONTINUOUS_HAZARD_LAMBDA`, sample counts, the four channel P&Ls, an
`EVIDENCE_LEVEL` and a `SUPPORT` flag. **No invented fields**: anything the
archive does not carry is the string `NOT_IDENTIFIED`.

**Account-specific AND cross-whale priors are both retained**, so BETTOR never
silently inherits Ferrari:

```
RN1_PRIOR  FERRARI_PRIOR  SWISSTONY_PRIOR  HRH_PRIOR
CROSS_WHALE_CONSENSUS_PRIOR
```

The consensus is **weighted by first-side acquisitions**, not averaged —
`UNWEIGHTED_AVERAGE_REFUSED = True`, and a test asserts the consensus lambda
lies inside its members' range. **swisstony is excluded from the consensus**,
not quietly averaged in, because it is excluded from clean ground truth.
Member lambdas are retained beside the consensus so disagreement survives
aggregation rather than being collapsed into it.

Sign agreement across all four is graded `HIGH_COHORT_SUPPORT` (4/4),
`MODERATE_COHORT_SUPPORT` (3/4) or `LOW_COHORT_SUPPORT` (2/4 or sign
conflict) — sign is descriptive, so the excluded account contributes to it.

```
PRIORS_ARE_INITIAL_ONLY = True
SUPERSEDED_BY           = BETTOR_POSTERIOR once shadow data is credible
```

## THE SELL FINDING, PRESERVED

```
WHALE_SELL_POLICY_GENERALIZABLE = NOT_ESTABLISHED
```

The cohort teaches us **where losses accumulated**. It does not supply a
solution, and BETTOR is not taught to copy whale exits. Two of four never sold;
the two that did placed sells that band-level settled economics do not explain.
This is a genuine gap in the archive and therefore an opportunity BETTOR must
close prospectively, not a behaviour to imitate.

## LESSON DISPOSITION MATRIX

| LESSON | SOURCE | EVIDENCE | BETTOR ADOPTION | IMPROVEMENT | VALIDATION |
|---|---|---|---|---|---|
| Merge channel positive cheap, negative expensive; settled the reverse | all 4 | band × channel, 4/4 | ADOPT as a prior | condition entry and exit on the band's channel profile | LEVEL_A |
| Settled channel can give back most of a pair engine | ferrari | −78.4% of merge | ADOPT as a constraint | separate ledgers, mandatory | LEVEL_A |
| Completion hazard decays by the minute | all 4 | grid, 10 horizons | ADOPT as a prior | time-dependent pairing prior | LEVEL_A |
| Basis/completion frontier is steep | ferrari | 9 ceilings × 10 T | ADOPT | choose basis on economics, not habit | LEVEL_A |
| ~24–32% of first legs never complete | all 4 | survival at settlement | ADOPT | residual is the base case, not the exception | LEVEL_A |
| Sells are not placed where settled economics are worst | rn1, ferrari | 252 sells | NOTE as hypothesis | BETTOR prices exits from the live book | LEVEL_B |
| Individual residual loss probability | — | band-level only | IMPROVE | BETTOR computes per position | LEVEL_B |
| Executable passive / aggressive exit | — | absent | REJECT archive; build native | live book | LEVEL_C |
| Optimal exit time | — | absent | REJECT archive; build native | EV comparison at each decision | LEVEL_C |
| Whale internal reasoning | — | absent | NOT_IDENTIFIED | never inferred | — |
| RN1 two-source band discrepancy | rn1 | 2.1–21.2 pp | REJECT any single RN1 band level as a quantity | — | open |
| swisstony's economics | swisstony | excluded | REJECT | — | excluded |

```
BETTOR_POLICY_IS_MORE_COMPLETE_AND_ECONOMICALLY_EXPLICIT_THAN_ANY_SINGLE
REFERENCE_POLICY = the engineering objective.

BETTOR_WILL_OUTPERFORM_THE_WHALES = NOT CLAIMED. Prospective evidence only.
```

```
No orders. No capital. No production activation. mirror_live = false.
```
