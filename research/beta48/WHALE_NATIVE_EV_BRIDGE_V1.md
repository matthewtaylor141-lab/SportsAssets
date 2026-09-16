# WHALE → NATIVE EV BRIDGE V1

```
OFFLINE.  VENUE_CONTACT = 0  ORDERS = 0  CAPITAL = 0  CREDENTIALS = NONE
mirror_live = false          run85 UNTOUCHED   rate experiment UNTOUCHED

PRIMARY    observed whale behaviour and reconstructed whale economics
SECONDARY  established public research
TERTIARY   BETTOR's own prospective observations and fills
```

Code: `shadow/whale_bridge.py`, `build_whale_priors.py`, `probe_blobs_v3.py`.
Data: `WHALE_REFERENCE_PRIORS_V1.json`, `BLOBS_V3_PROBE_V1.json` (both derived,
neither authored).

## 0. THE SCOPE THAT TRAVELS WITH EVERY NUMBER BELOW

```
PRIMARY_WHALE_PRIOR_ACCOUNTS  rn1, ferrarichampions2026, homerunhazard
SWISSTONY_STATUS              GHOST_PRIOR_SENSITIVITY_ONLY
WHALE_SELECTION_CONDITION     OBSERVED_WHALE_ENTERED_POSITIONS_ONLY
INDEPENDENT_EFFECTIVE_N       NOT_IDENTIFIED
PRICE_TIME_JOINT_PRIOR        NOT_IDENTIFIED
BETTOR_P_FILL_SOURCE          BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED
WHALE_COMPLETION_AS_P_FILL    FORBIDDEN
PAIR_BASIS_ABOVE_PAR_IS       GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES
BLOBS_V3_RICH_CELL_STATUS     NOT_AVAILABLE_AGGREGATE_ONLY
```

**The canonical prior is three accounts.** swisstony was `FLAGGED_EXCLUDED` in
`cross_account_table.ROLE` at commit `ee7329c`, before any hazard curve was
cut. The retained priors say `WHICH_FIELDS_PREVENT_INCLUSION:
NOT_IDENTIFIED_IN_THIS_WORKSPACE`, but the discriminator's own gate report does
name the defect, so the **field** is reconstructible even though the
**resolution** is not:

> `BETA48_DATA_GATE.md`, `BIGGEST_DISCREPANCY` item 1 — swisstony's pair (merge)
> channel is a **sign flip between two sources**. The external report puts it at
> −$3,390,000 (−0.7%); this reconstruction measures +$260,123 (+0.104%). Both
> are near zero against a $249,117,712 traded stake. Its *directional* channel
> reconciles almost exactly ($23,357,748 vs $22,160,000), so the disagreement is
> specific to the pair channel and is not a coverage artefact.

That is precisely the quantity the merge-sign consensus is built on. This
workspace holds only one of the two sources and cannot adjudicate between them,
so `SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY = NOT_ESTABLISHED`. **A reconciliation
residual of $0.00 does not clear it** — that proves the account re-sums to
itself, which is a different defect from disagreeing with an independent source
about a sign. Both priors are emitted: `WHALE_PRIOR_3_ACCOUNT` (canonical) and
`WHALE_PRIOR_4_ACCOUNT_SENSITIVITY` (never promoted, and its ghost account buys
no `SUPPORTING_ACCOUNTS` count, or the exclusion would be cosmetic).

**Every cell is conditioned on whale-selected entries.** The sample is positions
the whales chose to open. The markets they looked at and passed, the prices they
refused, the moments they stood aside — none of it is in the data. So a band
row is a statement about *retained whale-entered positions in that band*, never
about the band. `band_statement()` will only phrase it the one legal way, and
`scan_for_unconditional_phrasing()` catches "price below 0.50 is profitable" and
its relatives by name. **Whale evidence can never create a trade by itself:**
`WHALE_EVIDENCE_ALONE_CAN_CREATE_A_TRADE = False`.

---

## 1. WHAT THE RETAINED WHALE ARTEFACTS ACTUALLY CONTAIN

Read from `evidence/whale_audit/whale_exit_priors_v1.json`, not from report prose.

| Account | Role | Fills | Positions | Fills/position | Range | Evidence |
|---|---|---:|---:|---:|---|---|
| rn1 | PRIMARY | 4,692,866 | 259,271 | 18.1 | 2025-07-09 → 2026-09-15 | LEVEL_A_DIRECT |
| ferrarichampions2026 | PRIMARY | 1,958,843 | 101,611 | 19.3 | 2026-03-31 → 2026-09-15 | LEVEL_A_DIRECT |
| homerunhazard | PRIMARY | 595,223 | 55,092 | 10.8 | 2026-04-24 → 2026-09-15 | LEVEL_A_DIRECT |
| swisstony | **GHOST / SENSITIVITY** | 6,059,643 | 367,896 | 16.5 | 2025-08-09 → 2026-08-26 | LEVEL_A_DIRECT, `SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY = NOT_ESTABLISHED` |

Note that the ghost account is the **largest** of the four by both fills and
positions. That is exactly why it has to be held out by name rather than left
to be picked up by a count.

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
`whale_bridge.cell_support()` returns that rather than interpolating one.

The cross product is refused by name. The source declares its own limit —
`JOINT_TIME_BASIS_GRANULARITY = ACCOUNT x INTERVAL (NOT x PRICE_BAND)` — so a
hazard for "0.00–0.10 contracts after four hours unpaired" was never measured,
and every route to inventing one asserts an interaction nobody observed:

```
PRICE_TIME_JOINT_PRIOR = NOT_IDENTIFIED
JOINT_PRIOR_CONSTRUCTION_FORBIDDEN
  MULTIPLICATION_OF_MARGINALS        assumes conditional independence
  INTERPOLATION_ACROSS_BANDS         assumes smoothness along an unmeasured axis
  CROSS_PRODUCT_TABLE_CONSTRUCTION   asserts one cell per pair
  ADDITIVE_DECOMPOSITION             is a model, and an unfitted one
```

`cell_support(account=…, price_band=…)` returns MEASURED. Add
`time_unpaired_interval=` to the same call and it returns `NOT_IDENTIFIED` with
the reason. `join_price_and_time()` exists solely so that a future caller
reaching for the joint finds a `JointPriorError` with an explanation instead of
writing the multiplication inline.

The recovery path is examined in §8 — where the claim that `blobs_v3` supplies
it is **tested and refuted**.

---

## 2. THE FINDING: A THREE-ACCOUNT CONSENSUS ON A SIGN

From `CHANNEL_BY_PRICE_BAND`. The ghost row is shown *below the rule* and
counts towards nothing.

```
MERGE CHANNEL SIGN          0.00-0.10  0.10-0.30  0.30-0.50  0.50-0.70  0.70-0.90  0.90-1.01
rn1                            POS        POS        POS        POS        NEG        NEG
ferrarichampions2026           POS        POS        POS        POS        NEG        NEG
homerunhazard                  POS        POS        POS        NEG        NEG        NEG
                            --------- unanimous ---------  disagree   --- unanimous ---
-------------------------------------------------------------------------- SENSITIVITY --
swisstony  (GHOST)             POS        POS        POS        NEG        NEG        NEG
```

**Among retained whale-entered positions, the MERGE channel was positive in
every band below 0.50 and negative in every band above 0.70, in all three
primary accounts, independently reconstructed.** Sign agreement across
accounts is a mechanism-level regularity in a way that any one account's
magnitude is not. `BAND_CONSENSUS.CONSENSUS_IS_ON = SIGN_ONLY_NOT_MAGNITUDE`.

**What that sentence is NOT.** It is not "buying below 0.50 is profitable". The
sample is positions these accounts *chose* to open; we never see the cheap
contracts they declined. The statement describes the retained cohort, and the
counterfactual — what an arbitrary entry at that price would have returned —
is `NOT_IDENTIFIED`. The sensitivity run does not change the shape.

### Why: the GROSS pair basis crosses par

```
MEAN_PAIR_BASIS             0.00-0.10  0.10-0.30  0.30-0.50  0.50-0.70  0.70-0.90  0.90-1.01
rn1                          0.93098    0.94213    0.95969    0.97399    0.98644    0.99825
ferrarichampions2026         0.93823    0.94620    0.96511    0.99148  * 1.00555  * 1.01177
homerunhazard                0.96769    0.96033    0.96690    0.99593  * 1.00584  * 1.01022
swisstony (GHOST)            0.97597    0.97881    0.98413    0.99098    0.99779  * 1.00932
                                                                  * = GROSS BASIS ABOVE PAR
```

A completed pair at basis > 1.00 **cost more, in gross trade prices, than the
$1 it redeems**. That is `GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES`.

**It is not an established net loss.** Rebates, maker-reward programmes, volume
tiers and any other incentive sit outside these numbers —
`FIELD_AVAILABILITY.FEE_REBATE = NOT_SEPARATELY_RETAINED` — so
`NET_OF_INCENTIVES_PAIR_OUTCOME = NOT_IDENTIFIED`. An account could in
principle have paid above par on the pair and still ended the band up.

RN1's maximum is 0.99825, so it never crosses. **That is the clearest observed
structural distinction in the retained aggregate data** between RN1 and the
others — a description of what the prices were, not a verdict on who traded
better, which the data cannot support.

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
homerunhazard                 0.2832     0.2040     0.2475     0.2498     0.2185     0.2375
swisstony (GHOST)             0.3639     0.1766     0.1308     0.1419     0.2223     0.7162
```

U-shaped in every account: completion is **hardest at the extremes**. So the
band with the best merge economics (0.00–0.10) is also the band where the other
side is least likely to come — RN1 completed only 45% of its own entries there.
**There is no free money here; the cohort trades basis quality against
completion probability**, and that trade is exactly what an EV comparison is
for.

**And this rate is a WHALE COMPLETION quantity, not a BETTOR fill
probability.** `1 − RESIDUAL_RATE` is how often *their* second leg arrived, on
legacy Polymarket, for an account whose order policy is `NOT_IDENTIFIED`, over
positions *they* chose to open. It says nothing about whether *our* resting
order, at *our* price, in *our* queue, on PMUS, executes. `whale_bridge`
enforces the separation: `WHALE_COMPLETION_AS_P_FILL = FORBIDDEN`, and
`assert_p_fill_source` raises on every completion field name.

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

## 5. NEITHER FILLS NOR POSITIONS ARE AN INDEPENDENT SAMPLE SIZE

`effective_n` refuses the fill count by name, and — this is the correction —
**it no longer returns a key called `EFFECTIVE_N` at all.**

```
HIERARCHY                  EVENT -> MARKET -> POSITION -> FILL
CLUSTER_LEVEL_PREFERRED    EVENT
CLUSTER_LEVEL_AVAILABLE    POSITION
EVENT_IDS_IN_ARTEFACTS     NOT_PRESENT

POSITION_LEVEL_N           259,271            (rn1, FIRST_SIDE_ACQUISITIONS)
INDEPENDENT_EFFECTIVE_N    NOT_IDENTIFIED
INDEPENDENT_N_UPPER_BOUND  <= POSITION_LEVEL_N
INTERVAL_WIDTH_STATUS      LOWER_BOUND_ON_TRUE_CLUSTER_ROBUST_WIDTH
WEIGHTING_STATUS           HEURISTIC, ANTI_CONFIDENCE_CAPPED
```

Two inflations, one after the other. RN1's 4,692,866 fills arise from 259,271
first-side acquisitions — **18.1 fills per position** — and treating fills as
independent would overstate precision by roughly √18 ≈ 4.3×. But the position
count is not the end of it: several positions can belong to one event, one team,
one correlated line move, and **there is no event id to group them by**. So
`FIRST_SIDE_ACQUISITIONS` is an **upper bound** on the independent count and
never a measurement of it. The old label `EFFECTIVE_N` claimed the very
independence the data cannot support, and it is withdrawn.

Consequences, both machine-enforced:

- Any interval computed at the position level is a **lower bound on the true
  cluster-robust width**. It may be reported; it may not be called the width.
- `blend_weights` marks a position-level weighting `HEURISTIC` and caps it at
  `ANTI_CONFIDENCE_WEIGHT_CAP = 0.95` in **either** direction. A bound cannot
  drive a component to certainty. Only a *declared* regime shift reaches
  0.0 / 1.0, because that is a statement about the world rather than an
  inference from a count.

An event-blocked bootstrap is the right method and it needs event ids. §8
establishes that `blobs_v3` does not have them either.

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

### First, a correction: `blobs_v3` does NOT unlock the cell space

An earlier version of this section said re-deriving from `blobs_v3` "unlocks
sport/league/market-type cells, event-level clustering, and an event-blocked
bootstrap". **That was asserted from the files' existence and their upstream
endpoint, not from their contents. The probe read the contents and refutes it.**
`probe_blobs_v3.py`, artefact `BLOBS_V3_PROBE_V1.json`:

```
GRANULARITY                 AGGREGATE        (same as whale_exit_priors_v1)
PER_POSITION_ROWS           NOT_PRESENT      no list of dicts > 1000 anywhere
TOP_LEVEL_KEYS              CENSUS, REFERENCE_ACCOUNT_REPLAY,
                            REFERENCE_ACCOUNT_COMPLETION,
                            MERGE_PNL_BY_OPEN_BAND,
                            PNL_BY_OPEN_BAND_ALL_CHANNELS
SOURCE_ENDPOINT             data-api.polymarket.com/activity?type=TRADE
BLOBS_V3_RICH_CELL_STATUS   NOT_AVAILABLE_AGGREGATE_ONLY
```

All fourteen probed fields are **ABSENT at 0% coverage**: PER_POSITION_ROWS,
MARKET_ID, EVENT_ID, ENTRY_TIMESTAMP, ENTRY_PRICE, SECOND_LEG_TIMESTAMP,
SECOND_LEG_PRICE, SPORT, LEAGUE, GAME_START, LIQUIDITY, BOOK_STATE,
ORDER_IDENTITY, FILL_IDENTITY.

Three of those verdicts are worth stating precisely, because the file *looks*
as though it carries them:

- **MARKET_ID / EVENT_ID.** `CENSUS` carries the *counts* — rn1 has 138,498
  distinct conditions and 138,498 distinct market slugs — and not one id. A
  count cannot be joined on. And `DISTINCT_CONDITIONS == DISTINCT_MARKET_SLUGS`
  in every file, so even the upstream grouping was by MARKET, never by EVENT.
- **SPORT.** There is a key called `BY_SPORT_OR_QUESTION`, and it is not a
  sport taxonomy. It is the **top 25 question titles truncated to 24
  characters**, covering **3.04%–20.06% of opens** depending on the account.
  The key collides many-to-one: `"Philadelphia Phillies vs"` collapses every
  Phillies fixture over the whole period into one row, opponent-blind and
  date-blind. Its keys also mix kinds — a market type
  (`"Games Total: O/U 2.5"`), a tournament (`"Wimbledon, Qualification"`) and
  a fixture prefix — so it is not a partition either.
- **BOOK_STATE / ORDER_IDENTITY.** The endpoint is a TRADE tape. A tape of
  executions does not contain the book, and `WHALE_ORDER_POLICY` stays
  `NOT_IDENTIFIED`.

**What the probe DID find**, reported as what it is rather than inflated: two
cells at **100% coverage in all six files** that the retained priors do not
carry — `ACCOUNT × FILL_SIZE_BUCKET` and `ACCOUNT × ISO_WEEK`. They are real
and they are still **account-level marginals, not a cross product**. Two
accounts also appear here that are not in the prior at all, `kch123` and
`w2c33`. All six `.sha256` files verify.

So the recovery path is **an upstream re-extraction, not a reprocessing**: the
ids existed at extraction time — CENSUS counted them — and were not written
down. That is new work against `data-api.polymarket.com`, retaining the
per-row condition id, market slug and timestamp *before* aggregation. It needs
no PMUS venue access, and it is not something these files can be made to give.

### The rest, unchanged

1. **Cause-specific rather than subdistribution hazard.** The retained lambda is
   a subdistribution quantity with `CAUSE_SPECIFIC_HAZARD: NOT_COMPUTED`;
   competing risks (completed / settled / sold) would separate them.
2. **Per-position rows** would let completion and residual be joined to the same
   position instead of compared as aggregates. They exist in neither retained
   artefact, so this too is blocked on the re-extraction above.
3. **Event ids**, which are what an event-blocked bootstrap and an
   `INDEPENDENT_EFFECTIVE_N` actually require. Until then
   `INDEPENDENT_EFFECTIVE_N = NOT_IDENTIFIED` and every interval width is a
   lower bound.

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
