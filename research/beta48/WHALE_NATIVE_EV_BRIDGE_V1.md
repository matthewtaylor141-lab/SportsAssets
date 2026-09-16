# WHALE → NATIVE EV BRIDGE V1

```
OFFLINE.  VENUE_CONTACT = 0  ORDERS = 0  CAPITAL = 0  CREDENTIALS = NONE
mirror_live = false          run85 UNTOUCHED   rate experiment UNTOUCHED

PRIMARY    observed whale behaviour and reconstructed whale economics
SECONDARY  established public research
TERTIARY   BETTOR's own prospective observations and fills
```

Code: `shadow/whale_bridge.py`, `build_whale_priors.py`.
Data: `WHALE_REFERENCE_PRIORS_V1.json` (derived, not authored).

---

## 1. WHAT THE RETAINED WHALE ARTEFACTS ACTUALLY CONTAIN

Read from `evidence/whale_audit/whale_exit_priors_v1.json`, not from report prose.

| Account | Fills | Positions | Fills/position | Range | Evidence |
|---|---:|---:|---:|---|---|
| rn1 | 4,692,866 | 259,271 | 18.1 | 2025-07-09 → 2026-09-15 | LEVEL_A_DIRECT |
| swisstony | 6,059,643 | 367,896 | 16.5 | 2025-08-09 → 2026-08-26 | LEVEL_A_DIRECT *(excluded from consensus)* |
| ferrarichampions2026 | 1,958,843 | 101,611 | 19.3 | 2026-03-31 → 2026-09-15 | LEVEL_A_DIRECT |
| homerunhazard | 595,223 | 55,092 | 10.8 | 2026-04-24 → 2026-09-15 | LEVEL_A_DIRECT |

**The artefact's own refusals, carried forward verbatim** (they are the scope):

```
GRANULARITY                            AGGREGATE
PER_POSITION_ROWS                      NOT_PRESENT
HISTORICAL_BOOK_STATE                  NOT_PRESENT
EV_EXIT_HISTORICAL                     NOT_IDENTIFIED
WHALE_SELL_POLICY_GENERALIZABLE        NOT_ESTABLISHED
TRUE_CAUSE_SPECIFIC_COMPLETION_HAZARD  NOT_IDENTIFIED
COMPETING_RISK_MODEL                   NOT_IDENTIFIED
BASIS_CEILING_LAMBDA_IS                SUBDISTRIBUTION_QUANTITY
JOINT_TIME_BASIS_GRANULARITY           ACCOUNT x INTERVAL (NOT x PRICE_BAND)
```

### The scope finding, stated plainly

**The cell space the master task asks for does not exist in the retained data.**
There is no sport, league, market type, time-to-event, pregame/live, size band,
market age, time of day or sequence position. There are no market ids and no
event ids. What exists is:

```
ACCOUNT x PRICE_BAND              channel economics   (6 bands x 4 accounts)
ACCOUNT x TIME_UNPAIRED_INTERVAL  completion hazard   (10 intervals x 4)
```

**and not their cross product.** Every richer cell is `NOT_IDENTIFIED`, and
`whale_bridge.cell_support` returns that rather than interpolating one. The
recovery path — re-deriving richer cells from `blobs_v3` — is named in §8 as
work, not claimed as done.

---

## 2. THE FINDING: A FOUR-ACCOUNT CONSENSUS ON A SIGN

From `CHANNEL_BY_PRICE_BAND`, all four accounts:

```
MERGE CHANNEL SIGN          0.00-0.10  0.10-0.30  0.30-0.50  0.50-0.70  0.70-0.90  0.90-1.01
rn1                            POS        POS        POS        POS        NEG        NEG
ferrarichampions2026           POS        POS        POS        POS        NEG        NEG
swisstony                      POS        POS        POS        NEG        NEG        NEG
homerunhazard                  POS        POS        POS        NEG        NEG        NEG
                            --------- unanimous ---------  disagree   --- unanimous ---
```

**Completion economics are positive below 0.50 and negative above 0.70, in
every account, independently reconstructed.** Sign agreement across four
accounts is a mechanism-level regularity in a way that any one account's
magnitude is not. `BAND_CONSENSUS.CONSENSUS_IS_ON = SIGN_ONLY_NOT_MAGNITUDE`.

### Why: the pair basis crosses par

```
MEAN_PAIR_BASIS             0.00-0.10  0.10-0.30  0.30-0.50  0.50-0.70  0.70-0.90  0.90-1.01
rn1                          0.93098    0.94213    0.95969    0.97399    0.98644    0.99825
ferrarichampions2026         0.93823    0.94620    0.96511    0.99148  * 1.00555  * 1.01177
swisstony                    0.97597    0.97881    0.98413    0.99098    0.99779  * 1.00932
homerunhazard                0.96769    0.96033    0.96690    0.99593  * 1.00584  * 1.01022
                                                                        * = ABOVE PAR
```

A completed pair at basis > 1.00 **cost more than the $1 it redeems**: a loss
locked in at the moment of completion, before fees. Three of four accounts did
this in their top bands. **RN1 never did — its maximum is 0.99825.**

Basis rises monotonically with the band in all four accounts. The reading —
recorded as `MECHANISM_READING_STATUS: HYPOTHESIS_CONSISTENT_WITH_THE_SIGNS`,
not as a demonstrated causal claim — is that **the band is the price of the
first leg acquired**. Acquire the cheap leg first and the pair completes at a
good basis; acquire the expensive leg first and it completes at or above par.

### And the tension that makes it a real decision

```
RESIDUAL_RATE               0.00-0.10  0.10-0.30  0.30-0.50  0.50-0.70  0.70-0.90  0.90-1.01
rn1                           0.5479     0.2205     0.1395     0.1458     0.2212     0.5050
ferrarichampions2026          0.6378     0.3564     0.2823     0.2775     0.2986     0.4553
swisstony                     0.3639     0.1766     0.1308     0.1419     0.2223     0.7162
homerunhazard                 0.2832     0.2040     0.2475     0.2498     0.2185     0.2375
```

U-shaped in every account: completion is **hardest at the extremes**. So the
band with the best merge economics (0.00–0.10) is also the band where the other
side is least likely to come — RN1 completes only 45% of them. **The cheap-leg
rule is not free money; it trades basis quality against completion
probability**, and that trade is exactly what an EV comparison is for.

---

## 3. FERRARI: THE FAILURE MODE, QUANTIFIED

Ferrari's 0.10–0.30 band:

```
MERGE_CHANNEL_PNL     +3,844,029      the completion mechanism WORKED
SETTLED_CHANNEL_PNL   -4,435,219      the residual inventory ate it
TOTAL_CHANNEL_PNL       -560,629      net negative
RESIDUAL_RATE              0.3564     vs RN1's 0.2205 in the same band
```

One headline number would have hidden this completely. It is the single
clearest argument for the architecture rule that **completion economics and
residual economics are never summed into one figure** — in code
(`incentive_split`-style separation) and in every report.

Ferrari's residual rate exceeds RN1's in five of six bands. Same mechanism,
worse inventory control.

---

## 4. WHAT THE FILLS DO NOT IDENTIFY

```
WHALE_FILL_STATE_DISTRIBUTION        IDENTIFIED_FROM_RECONSTRUCTION
WHALE_ORDER_POLICY                   NOT_IDENTIFIED
WHALE_OPPORTUNITY_SELECTION_POLICY   NOT_IDENTIFIED
```

The archive observes fills and economic outcomes. It does not observe resting
quotes that never filled, cancels, missed fills, declined opportunities, the
contemporaneous queue, alternative actions, or any internal model prediction.

**A fill is the intersection of their intention and someone else's.** Without
the unfilled quotes the intention is not recoverable, and millions of observed
fills do not identify one unobserved decision. This is why BETTOR may copy a
*mechanism* and may not claim to have recovered a *policy*.

---

## 5. MILLIONS OF FILLS ARE NOT MILLIONS OF OBSERVATIONS

`effective_n` refuses the fill count by name and returns `NOT_IDENTIFIED` when
nothing below FILL is available.

```
HIERARCHY                 EVENT -> MARKET -> POSITION -> FILL
CLUSTER_LEVEL_PREFERRED   EVENT
CLUSTER_LEVEL_AVAILABLE   POSITION
EVENT_IDS_IN_ARTEFACTS    NOT_PRESENT
```

RN1's 4,692,866 fills arise from 259,271 first-side acquisitions — **18.1 fills
per position**. Treating fills as independent would overstate precision by
roughly √18 ≈ 4.3×. And positions within one event are themselves correlated
with no event id to cluster on, so a position-clustered interval is a **lower
bound** on the true width: `EFFECTIVE_N_IS_A_FLOOR` and
`UNCERTAINTY_METHOD = POSITION_CLUSTERED_BINOMIAL_WITH_EVENT_INFLATION_UNKNOWN`.

An event-blocked bootstrap is the right method. It needs event ids. That is §8
work.

---

## 6. MECHANISM TAXONOMY — THE WHALES ARE NOT ONE STRATEGY

| Account | Mechanisms |
|---|---|
| rn1 | TWO_SIDED_PASSIVE_MARKET_MAKING, COMPLETION, CAPITAL_RECYCLING, RESIDUAL_INVENTORY_RISK |
| ferrarichampions2026 | TWO_SIDED_PASSIVE_MARKET_MAKING, COMPLETION, RESIDUAL_INVENTORY_RISK |
| homerunhazard | DIRECTIONAL_PRICING, SETTLEMENT_HOLD, COMPLETION |
| swisstony | LIVE_DIRECTIONAL_PRICING, IN_PLAY_EXECUTION, HEDGE_INVENTORY_CLOSE, CAPITAL_RECYCLING |

`poolable()` refuses to combine COMPLETION with DIRECTIONAL_PRICING, and
TWO_SIDED_PASSIVE_MM with LIVE_DIRECTIONAL_PRICING: **different estimands.**
RN1/Ferrari completion evidence answers "will the other side come to me, and at
what basis". HRH directional evidence answers "is this contract mispriced".
Averaging them because the sport or the price matches produces a number about
nothing. Any pair not explicitly declared poolable returns
`NOT_ESTABLISHED_THAT_THESE_ARE_THE_SAME_ESTIMAND` — absence of a rule is not
permission.

---

## 7. VENUE-MECHANISM EQUIVALENCE

The archive is legacy Polymarket: two tokens per market, a pair completed by
holding both and merging. PMUS is one book per market with a long and a short
side.

| Mechanism | Equivalence | Why |
|---|---|---|
| RESIDUAL_INVENTORY_RISK | STRONG | one-sided exposure is one-sided exposure |
| DIRECTIONAL_PRICING | STRONG | a mispriced binary is venue-independent |
| SETTLEMENT_HOLD | STRONG | terminal payoff mechanics carry over |
| LIVE_DIRECTIONAL_PRICING | STRONG | |
| COMPLETION | **PARTIAL** | same economic payoff, **not** the same implementation, fill behaviour or queue mechanics |
| TWO_SIDED_PASSIVE_MM | PARTIAL | |
| CAPITAL_RECYCLING | PARTIAL | |
| IN_PLAY_EXECUTION | PARTIAL | |
| HEDGE_INVENTORY_CLOSE | **NOT_IDENTIFIED** | no external hedge instrument established for PMUS |

`SAME_PAYOFF_DOES_NOT_IMPLY_SAME_EXECUTION = True`. "Hold both legs" mapping to
"flatten the book" is a statement about payoff. It says nothing about whether a
resting order fills, how long it waits, or where it sits in a queue — and those
are what determine whether the mechanism earns anything here.

---

## 8. WHAT WOULD MAKE THIS BRIDGE STRONGER (named as work, not claimed)

1. **Re-derive cells from `blobs_v3` with market and event identity retained.**
   That unlocks sport/league/market-type cells, event-level clustering, and an
   event-blocked bootstrap. Largest single improvement available offline.
2. **Cause-specific rather than subdistribution hazard.** The retained lambda is
   a subdistribution quantity with `CAUSE_SPECIFIC_HAZARD: NOT_COMPUTED`;
   competing risks (completed / settled / sold) would separate them.
3. **Per-position rows** would let completion and residual be joined to the same
   position instead of compared as aggregates.

None of these needs venue access. All are blocked on reprocessing the raw
reconstruction, not on new data.

---

## 9. HOW THE PRIOR ENTERS AND HOW IT LEAVES

Whale evidence is a prior on **named components**, never a blended score.
`NO_SINGLE_BLENDED_SCORE = True`; there is no "EV = 50% whale + 50% BETTOR".

```
PRIOR_COMPONENTS = P_COMPLETION, COMPLETION_HAZARD, PAIR_CLOSE_COST,
                   RESIDUAL_OUTCOME, SIZE_CAPACITY, TIME_IN_INVENTORY,
                   MARKET_CLASS_PERFORMANCE
```

`blend_weights(prior_n, native_n, k)` gives `NATIVE_WEIGHT = n / (n + k)`. Two
properties matter and both are pinned by tests:

- **The prior's own size does not buy it weight.** A whale n of 10⁹ and a whale
  n of 100 produce the *same* native weight, because the weight depends on
  BETTOR's evidence against a credibility constant. A large archive cannot
  outvote BETTOR forever.
- **A declared regime shift zeroes the prior rather than decaying it.** If the
  world changed, old evidence is not weak evidence about the new world — it is
  evidence about a different one.

`PRIOR_DECAY_REASON` ∈ NATIVE_EVIDENCE_ACCUMULATED, REGIME_SHIFT,
HISTORICAL_EDGE_DECAYED, VENUE_MECHANICS_DIFFER, CURRENT_EVIDENCE_CONTRADICTS_PRIOR.

Both counts passed to `blend_weights` must be **effective** counts. A caller who
passes a fill count has reintroduced the error §5 exists to prevent, and is not
protected by the function.
