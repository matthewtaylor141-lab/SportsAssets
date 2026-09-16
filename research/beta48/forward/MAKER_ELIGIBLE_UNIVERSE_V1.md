# MAKER_ELIGIBLE_UNIVERSE_V1 — PREREGISTRATION

A separate experiment from the salted-hash generalization panel. Written and
committed BEFORE any economics are evaluated against these tiers.

**The gate this serves.** Not "is the average PMUS market profitable to make?"
but: *is there a PRE-IDENTIFIABLE market subset with positive expected net
economics and enough opportunity to matter?* A viable system may quote a small
fraction of the board.

---

## 0. A VALIDITY DEFECT IN THE PLANNED GENERALIZATION PANEL

Found before running it, and it changes what the next experiment has to be.

**The depth panel's sampling frame is the INCENTIVIZED universe, not the
board.** `panel_cohort` builds strata from `/v1/incentives` programmes
intersected with the board. From segment 8's own captured rules:

```
INCENTIVIZED_MARKETS_OBSERVED      100   (53 ufc + 47 dwts)
INCENTIVIZED_AND_ON_BOARD_PREFIX    53   ALL UFC
PANEL_SAMPLING_FRAME_SHARE          53 / 20,000 = 0.265% of the prefix
BOARD_DISTINCT_SPORT_TOKENS        126
```

The 47 DWTS markets are `INSTRUMENT_STATE_CLOSED` and so never reach the
panel. Every market the panel can possibly draw is a UFC fight.

**Therefore the salted-hash panel cannot produce cross-sectional
generalization.** `MAX_SINGLE_SPORT_SHARE` will be 100% by construction. The
hash rule fixes *ranking* bias within a frame; it cannot fix a frame that
contains one sport. Running it is still worth the requests — it validates the
hash mechanism and gives a second, differently-drawn sample of the same frame —
and it has been dispatched unchanged, with the salt untouched. But its result
must not be read as board evidence.

```
SALTED_HASH_PANEL_ANSWERS      DOES THE HASH RULE DRAW DIFFERENTLY WITHIN THE
                               INCENTIVIZED FRAME?
SALTED_HASH_PANEL_DOES_NOT_ANSWER
                               IS THE UFC RESULT REPRESENTATIVE OF THE BOARD?
```

**A business finding falls out of the same fact.** The observed liquidity
incentive programme covers 53 open markets out of ≥20,000. Model B below —
incentive-supported making — has almost no universe to harvest, and none
outside UFC. `INCENTIVES_PAGINATION_ADVANCED = NO`, so 100 may be a prefix;
`TRUE_INCENTIVIZED_UNIVERSE_SIZE = NOT_IDENTIFIED`. What is certain is that
nothing outside UFC was observed to be incentivized.

The genuine generalization instrument is a **board-frame panel**, stratified on
decision-time BOARD facts, drawing from all 20,000 rows. That is a new
preregistered experiment, not a retune of the frozen one.

---

## 1. WHAT IS OBSERVABLE BEFORE QUOTING, AND WHAT IT COSTS

Two stages, because they have different prices.

**STAGE 1 — free, from the board row already captured** (20,000 rows, zero
extra requests): `board_bestBidQuote`, `board_bestAskQuote`,
`orderPriceMinTickSize`, `sportsMarketTypeV2`, `marketType`, `status`,
`gameStartTime`, `endDate`, `minimumTradeQty`, sport/league token, implied mid.

**STAGE 2 — one book read per surviving market, at 2 rps**: full depth both
sides (queue ahead), and the `stats` block — `sharesTraded`, `notionalTraded`,
`openInterest`, `lastTradePx`, `lastTradeQty`, **`lastTradeSetTime`**.

`lastTradeSetTime` is the important one. A single read yields TIME SINCE LAST
TRADE, which is a pre-quote observable and is the closest available proxy for
the arrival rate §10 measured as decisive. It costs one request, not a time
series.

```
STAGE_1_COST   0 extra requests
STAGE_2_COST   1 request per market; 12,313 two-sided markets = ~1.7 h at 2 rps
```

**Observed board calibration (segment 8, description only, no economics):**

```
TWO_SIDED_BBO_COVERAGE   12,313 / 20,000 = 61.6%
TICK_SIZES               0.01 (19,132), 0.005 (576), 0.001 (292)
SPREAD_IN_TICKS          median 1, p25 1, p75 5
  <= 1 tick   8,201 = 66.6% of two-sided
  <= 2 ticks  8,623 = 70.0%
  <= 5 ticks  9,267 = 75.3%
  >  20 ticks 1,483 = 12.0%
TWO_SIDED_BY_SPORT       nfl 4,373, cfb 3,899, ushrmov 754, epl 397, ufc 355,
                         mlb 318, ushr 263, nba 124, lal 122, oscars 85
```

**The spread does not discriminate.** Two thirds of two-sided markets already
quote a one-tick spread — the same spread the UFC panel showed while producing
one trade per 26 minutes. A screen built on spread alone would admit most of
the board and reproduce §10's result at scale. **Activity, not spread, is the
variable that has to do the work**, and activity lives in stage 2.

---

## 2. THE FROZEN TIERS

Three, motivated operationally and cut at round numbers on the observed
distribution. Not searched. Not P&L-derived — no economics have been evaluated
against any of them at the time of writing.

```
TIER 0  BROAD
  status OPEN, two-sided BBO present, spread <= 5 ticks
  expected share ~75% of two-sided  (~9,300 markets)
  purpose: the denominator. Establishes what "quote everything reasonable"
           would occupy, so the narrower tiers are measured against it.

TIER 1  ACTIVE
  BROAD, plus stage 2: traded at least once in the last 24 h
  (lastTradeSetTime within 24 h of the read)
  expected share NOT_IDENTIFIED until stage 2 runs
  purpose: removes markets that are quoted but not traded. §10's panel would
           mostly have SURVIVED this, which is the point of including it -
           a tier that only excludes the obviously dead is a weak screen and
           must be shown to be weak rather than assumed strong.

TIER 2  HIGH_ACTIVITY
  ACTIVE, plus stage 2: traded within the last 60 MINUTES
  expected share NOT_IDENTIFIED
  purpose: the tier §10 predicts is necessary. At 1 trade per 26 minutes the
           UFC panel sits near this boundary, so this is the tier that decides
           whether a tradeable subset exists at all.
```

Cut points are 5 ticks (the p75 of the observed spread), 24 hours and 60
minutes. They are round, operationally meaningful, and fixed here.

**Anti-threshold-mining rules, binding:**

- These three are evaluated and reported **together, always**. A tier is never
  dropped from a report because it looked bad.
- No fourth tier is added after seeing results. If the three all fail, that is
  the finding — "no eligible subset was identified at the preregistered tiers"
  — not an invitation to search a fourth.
- Cut points do not move. A tier whose cut point is later found wrong is a NEW
  preregistered experiment with a new name and its own freeze commit.
- The calibration window describes the board's distribution only. No P&L,
  markout, settlement or future price entered the choice of any cut point.

**Forbidden inputs, in any tier, now or later:** future fills, future markouts,
future P&L, settlement, future price movement, post-entry completion, outcome.

---

## 3. WHAT EACH TIER MUST REPORT

Per tier, all of them, whatever they say:

```
MARKETS_ELIGIBLE_PER_DAY =
INDEPENDENT_EVENTS_PER_DAY =
TRADES_PER_MARKET_HOUR =
QUEUE_AHEAD =
EXPECTED_WAIT_TO_COUNTERFACTUAL_FILL =
SPREAD_CAPTURE =
MAKER_REBATE =
VERIFIED_INCENTIVES =
ADVERSE_SELECTION =
ONE_SIDED_INVENTORY_DURATION =
CAPITAL_OCCUPANCY_HOURS =
EXPECTED_NET_PNL_PER_FILL =
EXPECTED_NET_PNL_PER_MARKET =
EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR   <- the north star
```

**The north star is dollars per working-capital dollar per hour**, not ROI per
completed trade. §10 is the argument for it: a $0.0153-per-contract round trip
is not obviously bad, and becomes bad when it occupies capital for three hours
to happen once. A 0.3% edge recycled hourly beats a 2% edge that locks capital
for days, and only the capital-time metric can see the difference.

`INDEPENDENT_EVENTS_PER_DAY` is separate from `MARKETS_ELIGIBLE_PER_DAY` on
purpose. Sixteen markets on one UFC card are not sixteen independent bets —
§10's panel was 16 markets across 6 events, and eight of those markets were
props on a single fight.

---

## 4. TWO BUSINESS MODELS, NEVER COMBINED

```
A. STRUCTURAL / TRADING-EDGE MAKER    TRADING_NET_EX_INCENTIVES > 0
                                      incentives are incremental
B. INCENTIVE-SUPPORTED MAKER          TRADING_NET_EX_INCENTIVES <= 0
                                      TOTAL_NET_INCL_VERIFIED_INCENTIVES > 0
```

Reported per candidate:

```
TRADING_EDGE_EX_INCENTIVES =
INCENTIVE_CONTRIBUTION =
TOTAL_NET =
DEPENDENCE_ON_INCENTIVES =
```

`fill_model_v2.reconstruct()` already refuses to sum while any term is
unmeasured and labels the B case `INCENTIVE_DEPENDENT` rather than a structural
edge.

**Model B has a universe problem before it has an economics problem.** 53 open
incentivized markets, all UFC, is the entire observed harvestable set. Model B
cannot scale past that unless the incentivized universe is larger than the
endpoint's non-advancing first page showed.

---

## 5. THE REBATE FLOOR — required input to any future micro-live design

The fee is banker-rounded to the cent **per fill**, and carries a `p(1-p)`
term, so the minimum clip that earns anything at all is U-shaped in price:

```
price   MIN_CONTRACTS   MIN_NOTIONAL   rebate at that clip
 0.01        41            $0.41            $0.01
 0.02        21            $0.42            $0.01
 0.05         9            $0.45            $0.01
 0.10         5            $0.50            $0.01
 0.20         3            $0.60            $0.01
 0.30         2            $0.60            $0.01
 0.50         2            $1.00            $0.01
 0.70         2            $1.40            $0.01
 0.80         3            $2.40            $0.01
 0.90         5            $4.50            $0.01
 0.95         9            $8.55            $0.01
 0.98        21           $20.58            $0.01
 0.99        41           $40.59            $0.01
```

Minimum notional is remarkably flat at the low end — about $0.41–$0.60 from
p=0.01 to p=0.30 — and rises steeply above p=0.80 because the contract itself
costs more.

```
VALIDATION_CLIP = max( MIN_CONTRACTS_FOR_NONZERO_MAKER_REBATE(price),
                       venue minimum executable size )
                  subject to a strict dollar-risk cap
```

A validation clip below this floor would test a mechanism the venue rounds to
zero — it would look like a maker engine earning no rebate, and the conclusion
would be an artifact of clip size. The floor is cheap: even at p=0.99 it is
~$41 of notional.

```
MICRO_LIVE_AUTHORIZED = NO
```

No order is placed. This section exists so the clip is derived from the fee
mechanics rather than chosen when the moment comes.

---

## 6. WHAT THIS DOES NOT CLAIM

```
GENERIC_PASSIVE_MAKING_ON_THE_UFC_PANEL = ECONOMICALLY_UNATTRACTIVE / BLOCKED
BOARD_WIDE_MAKER_ENGINE                 = NOT_ESTABLISHED
MARKET_SELECTION                        = FIRST_CLASS_PROBLEM
UFC_RESULT_REPRESENTATIVE               = NOT_IDENTIFIED
```

One UFC card is not exchange-wide evidence, and §10 is not a refutation of the
maker architecture. It is a measurement of one corner of the board, and the
corner it measured was the only corner the instrument could reach.

---

# PRE-RESULT CLARIFICATION — no economics have been evaluated

Recorded BEFORE any tier was scored against any outcome. Four of these correct
overstatements in the text above; they are corrections, not retunes, and the
cut points are unchanged.

## CL-1. The incentive-universe finding was overstated

I wrote that Model B "has almost no universe to harvest and none outside UFC".
That is stronger than the evidence. `INCENTIVES_PAGINATION_ADVANCED = NO`, so
100 markets is what the endpoint served, not what exists.

**Supported:**

```
OBSERVED_OPEN_INCENTIVE_UNIVERSE          53 UFC MARKETS
OBSERVED_INCENTIVE_COVERAGE_LOWER_BOUND   53 / 20,000 OBSERVED PREFIX MARKETS
                                          = 0.265%, an observed lower bound
                                          from a captured page, NOT an
                                          exchange-wide prevalence estimate
```

**Not supported, and explicitly not concluded:**

```
TRUE_ACTIVE_INCENTIVE_UNIVERSE_SIZE   NOT_IDENTIFIED
INCENTIVE_ENDPOINT_COMPLETE           NOT_ESTABLISHED
NON_UFC_ACTIVE_INCENTIVES             NOT_OBSERVED, NOT PROVEN ABSENT
INCENTIVE_PROGRAM_BOARD_SHARE         NOT_IDENTIFIED (the denominator,
                                      TRUE_ACTIVE_BOARD_SIZE, is itself
                                      NOT_IDENTIFIED)
```

Both the numerator and the denominator of any "programme prevalence" figure are
prefixes. A ratio of two prefixes is not a share of anything.

## CL-2. Tier semantics: NESTED, and now tested

The intended design is nested, and the text above already read that way. It is
now stated unambiguously and asserted over every market rather than promised:

```
BROAD         = STATUS_OPEN AND TWO_SIDED_BBO AND SPREAD_TICKS <= 5
ACTIVE        = BROAD AND TRADE_RECENCY <= 24 h
HIGH_ACTIVITY = BROAD AND TRADE_RECENCY <= 60 min

HIGH_ACTIVITY subset ACTIVE subset BROAD    for every market
```

`HIGH_ACTIVITY` is defined against BROAD rather than against ACTIVE. Because
60 min < 24 h the two readings coincide today, and anchoring to BROAD means the
subset property survives a later edit to one threshold and not the other.
`ELIGIBILITY_TIERS_NESTED = YES`, pinned by a sweep over spread × recency ×
status in `test_eligibility.py`.

## CL-3. The stage-2 request count was wrong — 12,313 is not 9,267

I wrote "12,313 two-sided markets = ~1.7 h at 2 rps". That used the TWO-SIDED
count as though it were the BROAD survivor count. It is not: BROAD also
requires spread ≤ 5 ticks.

```
STAGE1_INPUT_COUNT            20,000   (19,999 MARKET_STATUS_OPEN)
STAGE1_TWO_SIDED_COUNT        12,313
STAGE1_BROAD_SURVIVOR_COUNT    9,267
STAGE2_BOOK_READ_COUNT         9,267   <- the frozen stage-2 population
WHY_STAGE2_COUNT_EQUALS_THIS   stage 2 reads exactly the BROAD survivors,
                               because ACTIVE and HIGH_ACTIVITY are defined
                               as subsets of BROAD and a read on a
                               non-survivor could not change any tier
STAGE2_DURATION_AT_2_RPS       1.29 h  (I said 1.7 h — 32.9% too many reads)
```

"Two-sided survivor" and "BROAD survivor" are not synonyms and are separate
fields in `census()`, with a test asserting they differ.

## CL-4. `lastTradeSetTime` is RECENCY, not an arrival rate

I called it "the closest available proxy for the arrival rate". That is wrong.
One timestamp establishes recency; a rate needs repeated observations or
timestamped executions.

```
TRADE_RECENCY_PROXY = CURRENT_TIME - lastTradeSetTime   <- cheap trade-recency
                                                           activity proxy
```

Nothing derives `TRADES_PER_HOUR`, `EXPECTED_INTERARRIVAL_TIME`, a Poisson
rate or a queue-clearing time from it — asserted structurally by a test that
those names do not occur in `eligibility.py` at all.

```
LEVEL_A_ACTIVITY   trade recency, from ONE book read     AVAILABLE
LEVEL_B_ACTIVITY   observed trade count / elapsed time   NOT AVAILABLE
                   (repeated sharesTraded deltas, a block-safe tape, or
                    streaming trades)

TRADE_RECENCY_AVAILABLE      YES
TRADE_ARRIVAL_RATE_AVAILABLE NO
```

A market with `LAST_TRADE_AGE = 5 min` can still have a terrible long-run
arrival rate — one trade in a day, read five minutes after it happened.

## CL-5. The north star is not manufactured from a recency proxy

```
EXPECTED_FILL_RATE                            NOT_IDENTIFIED
EXPECTED_WAIT_TO_FILL                         NOT_IDENTIFIED
EXPECTED_CAPITAL_OCCUPANCY                    NOT_IDENTIFIED
EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR  NOT_IDENTIFIED
```

Descriptive quantities may be reported per tier — `QUEUE_AHEAD`,
`LAST_TRADE_AGE`, `SPREAD`, `DEPTH`, `CURRENT_INCENTIVE_STATE`. The north star
stays `NOT_IDENTIFIED` while its denominator needs an unmeasured fill or
recycling rate, and `census()` hard-codes those four to `NOT_IDENTIFIED` with
a test that a market trading one second ago still yields no wait estimate.

## CL-6. Two separate objects, not one

**A. `BOARD_FRAME_GENERALIZATION_PANEL`** — answers whether the UFC §10
microstructure is representative. Frame: all eligible OPEN markets in the
observed board prefix, **not** incentives ∩ board. Selection: frozen
deterministic hash over preregistered strata. A manageable deterministic
sample, not all 20,000. Reports representation by sport, league, event, market
type, tick size, spread band, and time-to-event where valid.

**B. `MAKER_ELIGIBLE_ROLLING_CENSUS`** — answers how many markets pass the
pre-quote screens. Scanning thousands of books at 2 rps takes over an hour, so
it is a **ROLLING CENSUS and never a simultaneous board snapshot**. Every row
carries `BOARD_CAPTURE_TIME`, `BOOK_RECEIPT_TIME`, `BOOK_TRANSACT_TIME`,
`LAST_TRADE_SET_TIME` and `TRADE_RECENCY_AT_RECEIPT`, because a 1.3-hour scan
observes different markets at different wall-clock times and that drift must
not be hidden. `census()` carries `OBSERVATION_TYPE = ROLLING_CENSUS`,
`IS_SIMULTANEOUS_BOARD_SNAPSHOT = False` and an explicit drift label.

## CL-7. Three questions, kept apart

```
GENERALIZATION  is the UFC microstructure representative?      panel A
ELIGIBILITY     how many markets pass the pre-quote screens?   census B
ECONOMICS       are those markets profitable to make?          NEITHER
```

Economics needs fill and adverse-selection evidence.
`QUESTION_ECONOMICS_ANSWERABLE_HERE = False`, as a flag rather than a caveat.

## What is unchanged

The three tiers, their cut points, the forbidden-input list, the
anti-threshold-mining rules, and the rebate floor all stand exactly as frozen.
`MICRO_LIVE_AUTHORIZED = NO`.
