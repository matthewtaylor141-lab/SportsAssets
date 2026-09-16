# WHALE_COHORT_LESSONS — what BETTOR takes, and from whom

**BETTOR does not copy one whale. BETTOR selects validated mechanisms across
the cohort.** Every account below has something it does well and something it
does badly, and the two are not the same across accounts. Copying any single
one wholesale imports its failure mode along with its edge.

Evidence base: run `35034361586`, six accounts, one common as-of
`2026-09-15T23:09:17Z`, every payload hash-verified against the SHA256 its own
runner wrote. All six reconcile to the estimator's own closed-lot totals at
**$0.00 on stake and P&L**. Plus run-level maker-side measurement on 112,553
RN1 trades across 9,337 independent conditions with one-hot settlements.

Nothing below is a live claim about what these accounts are doing now. It is a
reconstruction of what they did, and the causal confidence is stated per row
rather than assumed.

---

## THE ONE-LINE FINDING THAT ORGANIZES EVERYTHING ELSE

The preregistered band relationship **replicated perfectly in the pair channel
and did not survive to money.**

```
MERGE channel, all six accounts   Spearman -0.94 to -1.00, one sign crossing
                                  each. Textbook.
TOTAL economics, same accounts    Spearman -0.26, -0.43, +0.03, -0.03,
                                  -0.77, -1.00. TWO of six clear -0.7.
                                  14 of 36 bands FLIP SIGN.
```

The gap is **survivorship**. 28%–81% of the cheapest opens never pair — the
highest residual rate of any band. Pairing succeeds least often exactly where
the merge edge looks best.

kch123's cheapest band is the whole lesson in one line:
**MERGE_ROI +59.49%, TOTAL_ROI −92.84%.** It merged $4,336 of stake profitably
while the legs that never found a complement settled for **−$142,150**.

---

## RN1

```
ROLE                          DISCOVERY (found the band relationship here;
                              cannot also confirm it)
ROWS                          4,692,866     RECONCILES  YES ($0.00/$0.00)
LIFETIME_ROI                  +4.86%  (+4.55% at a common band mix)
REALIZED_MERGE_PNL            +$10.36M

OBSERVED_PROFIT_MECHANISM     Informed directional taking. Measured from the
                              other side: a maker resting an offer that RN1
                              lifts earns -0.0090/share, 95% CI
                              [-0.0143,-0.0038], clustered by condition. RN1
                              is the mirror image, +0.0090/share. He is paid
                              for being right, not for providing liquidity.
OBSERVED_FAILURE_MODE         Band economics are monotone decreasing and cross
                              zero: cheap opens pay, expensive opens lose. He
                              is not uniformly good; he is good cheap.
                              Separately, his two independent measurements of
                              the same LIFETIME band split disagree by 2.1 to
                              21.2 pp in EVERY band
                              (RN1_TWO_SOURCE_COVERAGE_DISCREPANCY, open).
PAIR_BEHAVIOR                 78,500 merges, ZERO sells. He exits by BUYING
                              THE COMPLEMENT, never by selling the leg.
DIRECTIONAL_BEHAVIOR          Primary. Takes at once; does not rest.
RESIDUAL_INVENTORY_BEHAVIOR   Held to settlement. No exit channel at all.
CAPITAL_RECYCLING_BEHAVIOR    Via merge/redeem, not via sale.
WHAT_BETTOR_ADOPTS            (1) Complement-buy as a first-class exit, not
                              only a sale. (2) Entry-price sensitivity as a
                              real economic gradient. (3) Take-at-once where
                              the edge is informational and perishable.
WHAT_BETTOR_REJECTS           (1) Holding every unpaired leg to settlement.
                              (2) Any pretence that his LIFETIME band levels
                              are a quantity — the two sources disagree.
                              (3) Making a market to flow like his.
CONFIDENCE_IN_CAUSALITY       MECHANISM: HIGH for the maker-side loss (large
                              n, CI excludes zero, clustered, fills observed
                              not modelled). ATTRIBUTION: LOW for why he is
                              right. No fair-value model of his is observed.
```

## ferrarichampions2026

```
ROLE                          VALIDATION (independent, pair-positive)
ROWS                          1,958,843     RECONCILES  YES ($0.00/$0.00)
LIFETIME_ROI                  +7.06%  (+6.23% at a common band mix) — BEST
REALIZED_MERGE_PNL            +$10.80M

OBSERVED_PROFIT_MECHANISM     Same shape as RN1 in the MERGE channel
                              (rho -1.0000, signs ++++--) and the highest
                              total ROI of the six. Independent of the
                              discovery account, which is what makes it the
                              validation row.
OBSERVED_FAILURE_MODE         Its TOTAL-economics rho is -0.2571 with signs
                              +-++-+ — the band relationship that is perfect
                              in its merges is ABSENT in its money. Its
                              0.10-0.30 band flips +32.34% MERGE to -2.00%
                              TOTAL.
PAIR_BEHAVIOR                 Pair-positive. Merges profitably.
DIRECTIONAL_BEHAVIOR          Present; not separable from pairing in this
                              evidence.
RESIDUAL_INVENTORY_BEHAVIOR   Settlement. SELL channel under 0.54% of stake.
CAPITAL_RECYCLING_BEHAVIOR    Merge-driven.
WHAT_BETTOR_ADOPTS            That a pair mechanism CAN be run at scale with
                              positive merge economics and a positive total —
                              it is the existence proof that the mechanism is
                              not inherently broken.
WHAT_BETTOR_REJECTS           Using its merge-channel band curve as an ENTRY
                              rule. Its own total economics refute that.
CONFIDENCE_IN_CAUSALITY       MEDIUM. Positive total is measured and
                              reconciled. WHY it is positive is not isolated:
                              band mix is refuted as the explanation
                              (ACCOUNT_MIX_EFFECT = REFUTED) and no
                              alternative is established.
```

## swisstony

```
ROLE                          FLAGGED_EXCLUDED from clean ground truth
ROWS                          6,059,643 (largest)   RECONCILES  YES
LIFETIME_ROI                  +0.10%  (+0.33% at a common band mix)
DAYS_SINCE_LAST_FILL          20.58

OBSERVED_PROFIT_MECHANISM     NOT_IDENTIFIED. It is the only account besides
                              RN1 whose TOTAL rho clears the preregistered
                              -0.7 bar (-0.7714), but it is excluded from
                              clean ground truth on an unresolved two-source
                              discrepancy. It reconciles internally; that does
                              not clear the exclusion.
OBSERVED_FAILURE_MODE         Roughly break-even at the largest row count in
                              the cohort: six million fills to make +0.10%.
                              Whatever it is doing does not scale into money.
PAIR_BEHAVIOR                 Merge rho -1.0000, signs +++---.
DIRECTIONAL_BEHAVIOR          NOT_IDENTIFIED.
RESIDUAL_INVENTORY_BEHAVIOR   Settlement.
CAPITAL_RECYCLING_BEHAVIOR    NOT_IDENTIFIED.
WHAT_BETTOR_ADOPTS            NOTHING. An excluded account contributes no
                              adopted mechanism, by rule, and the fact that
                              its numbers happen to look supportive is exactly
                              why the rule exists.
WHAT_BETTOR_REJECTS           Volume as a proxy for edge. It has the most
                              fills and almost no profit.
CONFIDENCE_IN_CAUSALITY       NONE. Excluded.
```

## homerunhazard

```
ROLE                          CONTROL (pair-negative)
ROWS                          595,223       RECONCILES  YES ($0.00/$0.00)
LIFETIME_ROI                  -1.04%  (-0.97% at a common band mix)

OBSERVED_PROFIT_MECHANISM     None that survives. Mildly negative.
OBSERVED_FAILURE_MODE         Its MERGE band curve is textbook (rho -0.9429,
                              signs +++---) and its money is negative. It is
                              the cleanest single demonstration that the merge
                              curve is not an edge.
PAIR_BEHAVIOR                 Pair-negative.
DIRECTIONAL_BEHAVIOR          Its 0.50-0.70 band flips -3.03% MERGE to +1.51%
                              TOTAL — its money came from the band its pairing
                              did worst in.
RESIDUAL_INVENTORY_BEHAVIOR   Settlement. SELL channel is EXACTLY ZERO — the
                              purest case of the cohort-wide no-exit pattern.
CAPITAL_RECYCLING_BEHAVIOR    None observable.
WHAT_BETTOR_ADOPTS            Nothing positive. It earns its place as the
                              control that kills the entry rule.
WHAT_BETTOR_REJECTS           The standalone band-entry rule, decisively.
CONFIDENCE_IN_CAUSALITY       HIGH for the NEGATIVE result. A control that
                              refutes is doing its job.
```

## kch123

```
ROLE                          CONTROL (pair-negative)
ROWS                          175,076 (smallest)    RECONCILES  YES
LIFETIME_ROI                  -6.61%  (-6.44% at a common band mix)
DAYS_SINCE_LAST_FILL          78.1  — DORMANT

OBSERVED_PROFIT_MECHANISM     None.
OBSERVED_FAILURE_MODE         THE definitive failure of the cohort.
                              0.00-0.10 band: MERGE_ROI +59.49%,
                              TOTAL_ROI -92.84%. $4,336 of stake merged
                              profitably; the legs that never paired settled
                              for -$142,150. A 33x loss ratio on the band that
                              looked best.
PAIR_BEHAVIOR                 Merge rho -0.9429, signs ++----. TOTAL rho
                              +0.0286 — the relationship is not merely weaker,
                              it is GONE.
DIRECTIONAL_BEHAVIOR          NOT_IDENTIFIED.
RESIDUAL_INVENTORY_BEHAVIOR   Held to settlement, catastrophically.
CAPITAL_RECYCLING_BEHAVIOR    None.
WHAT_BETTOR_ADOPTS            Its failure, as a hard design constraint:
                              RESIDUAL_INVENTORY_PNL is a SEPARATE LEDGER and
                              a profitable pair channel never offsets it in a
                              headline.
WHAT_BETTOR_REJECTS           Entering cheap legs to harvest pair completion.
                              Also: it is the account that exposed the
                              5-cluster significance defect (D1) and the
                              per-account regime-window defect (D2). Both
                              repaired; both are standing reminders that a
                              narrow confidence interval on five observations
                              is noise wearing a label.
CONFIDENCE_IN_CAUSALITY       HIGH. The mechanism of the loss is explicit and
                              arithmetic: unpaired legs settled at zero.
```

## w2c33

```
ROLE                          CONTROL (pair-negative)
ROWS                          623,639       RECONCILES  YES ($0.00/$0.00)
LIFETIME_ROI                  -8.04%  (-7.58% at a common band mix) — WORST

OBSERVED_PROFIT_MECHANISM     None.
OBSERVED_FAILURE_MODE         Worst total ROI in the cohort with a textbook
                              merge curve (rho -0.9429, signs ++----) and a
                              TOTAL rho of -0.0286.
PAIR_BEHAVIOR                 Pair-negative. Its 0.10-0.30 band flips +13.83%
                              MERGE to -4.94% TOTAL.
DIRECTIONAL_BEHAVIOR          Its 0.50-0.70 band flips -8.64% MERGE to +5.90%
                              TOTAL — again, money from where pairing failed.
RESIDUAL_INVENTORY_BEHAVIOR   Settlement.
CAPITAL_RECYCLING_BEHAVIOR    None.
WHAT_BETTOR_ADOPTS            Nothing positive.
WHAT_BETTOR_REJECTS           Same as homerunhazard, independently. Two
                              controls failing the same way is not one
                              observation twice.
CONFIDENCE_IN_CAUSALITY       HIGH for the negative result.
```

---

## CROSS-ACCOUNT MATRIX

| | RN1 | ferrari | swisstony | hrh | kch123 | w2c33 |
|---|---|---|---|---|---|---|
| role | DISCOVERY | VALIDATION | EXCLUDED | CONTROL | CONTROL | CONTROL |
| reconciles $0.00 | YES | YES | YES | YES | YES | YES |
| lifetime ROI | +4.86% | **+7.06%** | +0.10% | −1.04% | −6.61% | **−8.04%** |
| ROI at common band mix | +4.55% | +6.23% | +0.33% | −0.97% | −6.44% | −7.58% |
| rho MERGE | −1.000 | −1.000 | −1.000 | −0.943 | −0.943 | −0.943 |
| rho TOTAL | −1.000 | −0.257 | −0.771 | −0.429 | +0.029 | −0.029 |
| clears −0.7 on TOTAL | YES | no | (excl.) | no | no | no |
| merge curve textbook | YES | YES | YES | YES | YES | YES |
| money follows the curve | YES | NO | — | NO | NO | NO |
| sells its legs | NO | NO | NO | **NEVER** | NO | NO |
| exits by complement | YES | — | — | — | — | — |
| dormant at as-of | no | no | 20.6 d | no | **78.1 d** | no |

**Read the last four rows together.** Six out of six produce the textbook merge
curve. One out of six has money that follows it. Nobody sells. That is a cohort
in which the *measurement* replicates and the *strategy* does not.

### What is common to all six, and therefore structural rather than personal

```
1. THE MERGE CURVE IS UNIVERSAL AND UNINFORMATIVE ABOUT MONEY.
   6/6 replicate it. 2/6 have total economics that agree with it.

2. NOBODY SELLS. SELL channel 0.00%-0.54% of lot stake across all six;
   homerunhazard exactly zero. There is no sample of exits anywhere in this
   cohort from which to price one.
      -> UNPAIRED_LEG_EXIT_COST = NOT_IDENTIFIED, and it is the biggest
         remaining unknown in the whole pair architecture.
      -> It may NOT be assumed cheap. A cheap leg goes unpaired precisely
         because no counterparty wants that side -- the same condition that
         makes it expensive to exit.

3. RESIDUAL INVENTORY IS WHERE THE MONEY GOES. 28%-81% of cheapest opens
   never pair. kch123 lost 33x its merge profit to settled residual.

4. A COMMON BAND MIX CHANGES NOTHING. Forcing all six to one stake mix moves
   every ROI by under a point and flips nobody. ACCOUNT_MIX_EFFECT = REFUTED.
   The 3/3 account-level sign split is NOT explained by where each account's
   merges sit.
```

### What differs, and is therefore a candidate mechanism rather than a law

```
RN1 and ferrari are profitable; hrh, kch123 and w2c33 are not. The cohort does
NOT identify why. Band mix is refuted. Sport/question is only PARTIAL (top-25).
League, market type, first-leg side, time-to-event and book state are all
NOT_IDENTIFIED. So:

   WHY_SOME_WHALES_PROFIT = NOT_IDENTIFIED

and BETTOR does not get to adopt "whatever RN1 does", because nobody has
isolated what that is.
```

---

## WHAT BETTOR ADOPTS, CONSOLIDATED

Each line names the account(s) it comes from and the confidence.

```
ADOPT  Complement-buy as a first-class exit alongside sale     RN1     HIGH
ADOPT  Entry-price sensitivity as a real economic gradient     6/6     HIGH
ADOPT  Separate ledgers for pair / residual / directional      kch123  HIGH
       (adopted from a failure, which is the strongest kind)
ADOPT  Take-at-once where edge is informational and perishable RN1     MEDIUM
ADOPT  NO-TRADE as a first-class action                        6/6     HIGH
       (4 of 6 would have been better off not opening)

REJECT Standalone band-entry to harvest pair completion    hrh/kch/w2c HIGH
REJECT Holding every unpaired leg to settlement                kch123  HIGH
REJECT Volume as a proxy for edge                           swisstony  HIGH
REJECT Making a market to informed flow at the touch              RN1  HIGH
       (-0.0090/share measured, CI excludes zero)
REJECT Quoting any single RN1 band level as a quantity            RN1  HIGH
       (its two sources disagree by up to 21.2 pp)
REJECT Copying any one account wholesale                        cohort HIGH
```

---

## THE MAKER-SIDE MEASUREMENT, BECAUSE IT DECIDES THE ENGINE QUESTION

Separate from the six-account reconstruction: 112,553 RN1 trades across 9,337
independent conditions, each with a one-hot settlement resolving strictly after
the trade. **Fills are observed, not modelled** — no touch is being counted as
a fill.

```
SPREAD_CAPTURE                    +0.0050   half the measured round trip
FAIR_VALUE_EDGE                   NOT_IDENTIFIED
ADVERSE_SELECTION                 -0.0140   residual: net minus spread
MAKER_REBATES                     NOT_IDENTIFIED at the time of that run
LIQUIDITY_REWARDS                 NOT_IDENTIFIED
------------------------------------------------------------------
EXPECTED_MAKER_NET_VALUE          -0.0090 per share
                                  95% CI [-0.0143, -0.0038], clustered
```

**Adverse selection is 2.8x the half-spread it earns.** The break-even rebate
would be 0.90 c/share = **1.81% of notional on a 50c contract**, which is not a
rebate schedule that exists.

Scope, stated precisely: this measures *making a market to RN1's flow at the
touch*. It does not say market making is unprofitable in general, and it is not
a measurement of BETTOR's fill probability — RN1's counterparties were filled;
BETTOR was not there. It is the cohort's single hardest constraint on the maker
engine and it is carried into `MAKER_ENGINE_GATE_V2` as such.

---

## PROVENANCE AND DISCIPLINE NOTES, KEPT WITH THE FINDINGS

- One threshold search was run in the whole exercise — the six-band unanimity
  sweep — and its multiple-comparisons cost is stated (9.1% chance at least one
  of six bands is unanimous under a naive null) rather than hidden.
- The one band that clears the unanimity bar (0.90–1.01) is recorded as
  `POST_HOC_OBSERVATION` and **not** promoted: its merge channel is negative in
  all six accounts, so it describes "buy a heavy favourite and hold to
  settlement" — a directional bet, not the pair mechanism.
- `PAIR_BASIS_DISCRIMINATOR = NOT_SUPPORTED` and `ACCOUNT_MIX_EFFECT = REFUTED`
  are two hypotheses closed on evidence and not quietly dropped.
- Evidence travels by git and nothing is retyped: hand-transcribing evidence
  out of a log failed on 2 of 4 chunks even with per-chunk hashes.

```
mirror_live = false. Read only. No order, no capital, no credential,
no production write. Track A untouched. Phase X untouched.
```
