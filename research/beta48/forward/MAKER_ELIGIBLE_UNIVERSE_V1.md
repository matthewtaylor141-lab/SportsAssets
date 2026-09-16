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

---

# PRE-RESULT CLARIFICATION 2 — TIME ALIGNMENT

Still no economics evaluated against any tier. Cut points unchanged.

## CL-8. Stage 1 and stage 2 observe DIFFERENT times, and were being mixed

Stage 1 reads the board at `T0`. Stage 2 reads each survivor's book at `Ti`,
up to 1.29 hours later. Combining `SPREAD_AT_T0` with
`TRADE_RECENCY_AT_Ti` describes no market at any single decision time.

So the two stages now have different jobs and different names:

```
STAGE1_ROUTING_SCREEN    decides which markets deserve the expensive read
STAGE2_DECISION_SCREEN   the contemporaneous trading decision, recomputed
                         ENTIRELY from the book that arrived at Ti
```

Stage 1 is a routing frame. It is not the final eligibility answer, and its
spread is never carried forward as though it were current.

**Every tier is recomputed at `Ti` from the arriving book:**

```
CURRENT_BID_AT_TI, CURRENT_ASK_AT_TI, CURRENT_SPREAD_TICKS_AT_TI,
CURRENT_STATE_AT_TI, LAST_TRADE_SET_TIME, BOOK_TRANSACT_TIME, BOOK_RECEIPT_TIME

BROAD_AT_DECISION         = open at Ti AND two-sided at Ti
                            AND current spread <= 5 ticks at Ti
ACTIVE_AT_DECISION        = BROAD_AT_DECISION AND recency <= 24 h
HIGH_ACTIVITY_AT_DECISION = BROAD_AT_DECISION AND recency <= 60 min
```

The nesting tests apply to the `_AT_DECISION` tiers exactly as before.

## CL-9. Censoring: stage 1 rejects are never re-examined

A market quoting 6 ticks at `T0` is never read at `Ti`, and may have tightened
to 1 tick in between. This pipeline cannot know, and no amount of stage-2 care
recovers it.

```
STAGE1_ROUTED_MARKETS                      countable
STAGE2_CURRENTLY_ELIGIBLE_MARKETS          countable
STAGE2_ELIGIBILITY_RATE_WITHIN_ROUTED_FRAME countable
TRUE_ELIGIBLE_MARKET_SHARE_OF_BOARD        NOT_IDENTIFIED
```

The result is a **PIPELINE_YIELD** — what this scanner surfaces — and never a
board prevalence. That is operationally honest: a production scanner really
does behave this way, and the number a production scanner produces is the
number worth knowing. It is simply not the same number as "how many markets on
the board are eligible", and the two must not share a name.

## CL-10. Recency needs an explicit clock, per row

Two figures, both computed where the inputs exist, neither from a shared
timestamp:

```
TRADE_RECENCY_AT_VENUE_SNAPSHOT  = BOOK_TRANSACT_TIME - LAST_TRADE_SET_TIME
TRADE_RECENCY_AT_BETTOR_RECEIPT  = BOOK_RECEIPT_TIME  - LAST_TRADE_SET_TIME
```

Never from `SCAN_END_TIME`, `REPORT_TIME`, or one clock reading applied to
every market — that would make the first market scanned look 1.29 hours staler
than the last purely because of scan order.

Asserted per row, with violations counted rather than clamped:

```
LAST_TRADE_SET_TIME <= BOOK_TRANSACT_TIME
BOOK_RECEIPT_TIME   >= BOOK_TRANSACT_TIME     (subject to clock semantics)
NEGATIVE_RECENCY_ROWS = <count>
```

**A negative age is a data-quality defect, not "very active."** Clamping it to
zero would turn a broken clock into the strongest possible activity signal, and
put exactly the wrong markets at the top of the screen.

## CL-11. "Board-frame" overstates it — the frame is the observed prefix

```
SAMPLING_FRAME                     OBSERVED_20K_PREFIX
FULL_BOARD_BOUNDARY_KNOWN          NO
TRUE_ACTIVE_BOARD_SIZE             NOT_IDENTIFIED
GENERALIZES_TO_OBSERVED_PREFIX     potentially testable
GENERALIZES_TO_FULL_BOARD          NOT_IDENTIFIED
```

A deterministic hash fixes selection WITHIN a frame. It cannot repair an
incomplete outer frame. The strongest positive result this experiment can ever
produce is `UFC_RESULT_REPRESENTATIVE_OF_OBSERVED_PREFIX = YES`;
`UFC_RESULT_REPRESENTATIVE_OF_BOARD` stays `NOT_IDENTIFIED` whatever it returns.

## CL-12. The free activity features DO NOT EXIST at runtime — probed, 0/20,000

A zero-network probe against the raw board capture already on disk (segment 8,
`board_raw.jsonl.gz`, 200 responses, 20,000 market objects). The documentation
lists volume fields; **the runtime response contains none of them.**

```
BOARD_VOLUME24HR_PRESENT      NO    COVERAGE 0/20,000
BOARD_VOLUME1WK_PRESENT       NO    COVERAGE 0/20,000
BOARD_VOLUME1MO_PRESENT       NO    COVERAGE 0/20,000
BOARD_VOLUME_NUM_PRESENT      NO    COVERAGE 0/20,000
BOARD_LIQUIDITY_NUM_PRESENT   NO    COVERAGE 0/20,000
BOARD_LAST_TRADE_PRICE_PRESENT NO   COVERAGE 0/20,000
(also absent: volume, liquidity, openInterest)
```

The 46 fields the runtime DOES return are enumerated in the probe output.
Notably `bestAskQuote` appears on 14,443 rows and `bestBidQuote` on 12,336 —
the two sides are not equally populated, which is why two-sided coverage
(61.6%) is below either.

**Consequence: there is no cheap stage-1 activity screen. The 9,267 book reads
stand.** This is the "a field in a schema is not a field in a response"
principle, confirmed rather than assumed — and it was worth 15 minutes to check
before spending 1.29 hours of requests on a plan that assumed otherwise.

Nothing in V1 changes. Two fields ARE present on every row and are recorded as
**candidate future stage-1 features, not admitted to V1**:

```
CANDIDATE_updatedAt     20,000/20,000, 30 distinct hour-buckets
CANDIDATE_ep3SyncedAt   20,000/20,000, 5 distinct hour-buckets
```

Both are ROW-UPDATE recency, not trade activity. `ep3SyncedAt` is nearly
constant (19,882 rows share one hour), so it carries almost no information.
`updatedAt` is more varied but still says when the venue touched the record,
which is not when anyone traded. Neither is admitted to V1, and neither would
be admitted later without its own freeze.

## CL-13. Cumulative fields are not interval volume until reset semantics are known

`sharesTraded` and `notionalTraded` are cumulative. Before any differencing:

```
MONOTONICITY_TEST         sharesTraded(t+1) >= sharesTraded(t)
RESET_SEMANTICS           NOT_IDENTIFIED — lifetime? session? business day?
DIFFERENCING_PERMITTED    NO, until reset semantics are established
```

A field that silently resets at the 17:00 ET business-day boundary would
produce a large negative delta that looks like nothing, and a large positive
one the next interval that looks like a burst of trading. If monotonicity holds
across the relevant period, repeated deltas may eventually give
`EXECUTED_SHARE_VOLUME_PER_INTERVAL` — and still not aggressor, and still not
block-safe CLOB attribution.

## CL-14. Three experiments, three questions, no substitution

```
A. GENERALIZATION  does UFC microstructure resemble the observed 20k prefix?
B. ROUTING/ELIGIBILITY  how many opportunities does the frozen scanner surface?
C. ECONOMICS  do surfaced opportunities generate positive net economics after
              fill probability, adverse selection, inventory and costs?
```

A does not answer B. B does not answer C. C needs evidence nothing above
provides.

---

# PRE-RESULT FREEZE 3 — ESTIMAND, EVENT IDENTITY, ROUTING AUDIT

Still no economics. Cut points unchanged.

## CL-15. The estimand: market-weighted and event-weighted, never collapsed

Uniform MARKET sampling answers "what does a randomly selected instrument look
like?" It does not answer "what does a randomly selected independent event look
like?", and it does not produce independent observations when sampled markets
share an event.

**Measured on the observed prefix, and the magnitude is not marginal:**

```
MARKETS_WITH_DERIVABLE_EVENT_KEY   19,513 / 20,000 = 97.6%
UNIQUE_EVENTS                       1,456
MARKETS_PER_EVENT                   mean 13.4, median 8, p90 27, MAX 394
MAX_SINGLE_EVENT_SHARE              394 / 19,513 = 2.02%
largest events   nfl-2027-01-10 394 | cfb-wins-2026-11-28 380 |
                 cfb-2026-11-28 261 | epl-2027-05-30 248
```

Treating 20,000 markets as 20,000 independent observations overstates the
effective sample by **more than an order of magnitude**, and one single event
holds 2% of the entire board.

```
MARKET_WEIGHTED_ESTIMAND   quote-opportunity count, routing workload,
                           number of books to make
EVENT_WEIGHTED_ESTIMAND    independent opportunity count, correlated
                           inventory, capital concentration, event capacity,
                           statistical independence
```

`by_event()` aggregates WITHIN event first and then weights events equally —
never by pretending each market is independent. `RAW_MARKET_N` and
`UNIQUE_EVENT_N` are separate fields; `INDEPENDENT_SAMPLE_SIZE` is the event
count. `inference_status()` returns `UNDERPOWERED` below 30 events rather than
manufacturing precision.

## CL-16. Event identity: the venue publishes none

Probed against the 20,000 captured raw market objects:

```
eventSlug 0/20,000   eventId 0/20,000   event 0/20,000   eventTicker 0/20,000
seriesId  0/20,000   groupId 0/20,000   parentId 0/20,000  gameId 0/20,000
conditionId 0/20,000
```

So the key is DERIVED, and the derivation is frozen in `event_key()` before any
sampling: first `YYYY-MM-DD` in the slug, keep through it, drop the leading
grammar prefix.

```
EVENT_ID_SOURCE            DERIVED_FROM_SLUG_DATE_PREFIX
EVENT_ID_COVERAGE          19,513 / 20,000 = 97.6%
MARKETS_WITHOUT_EVENT_ID   487
```

A market with no derivable key returns `None` — **not a fabricated singleton**.
It stays a market-level observation and is excluded from event-clustered
inference with a count, because inventing an event for it would make the very
markets we cannot place look maximally independent.

## CL-17. The generalization frame is OPEN markets, not two-sided ones

Two-sidedness is itself an economically relevant property of the board —
38.4% of the observed prefix does not have it. Conditioning the generalization
panel on two-sided quotes would discard the finding and then be unable to
report it.

```
OBSERVED_PREFIX_FRAME_PANEL frame = OPEN markets in the observed prefix
observable and reported:  TWO_SIDED | ONE_SIDED | EMPTY_UNQUOTED |
                          CLOSED_AT_DECISION | INVALID
```

If a panel IS conditioned on two-sided markets it is labelled
`TWO_SIDED_MARKET_FRAME` and its prevalence is not generalized back to all OPEN
instruments.

## CL-18. ROUTING_CENSORING_AUDIT_V1 — preregistered

Stage 1 rejects spread > 5 ticks at `T0` and never looks again, so a market
that tightened before `Ti` is invisible. The audit measures how often that
happens, without reading every rejected market.

```
FRAME       stage-1 OPEN two-sided markets rejected ONLY for spread > 5 ticks
SELECTION   deterministic salted hash, fixed before any stage-2 result
AUDIT_N     300      <- fixed on request budget, NOT on observed miss rate
SALT        BETA48-FORWARD-PANEL-2026-09-16   (the existing frozen salt)
COST        300 reads = 2.5 min at 2 rps, on top of the 9,267
```

At each audit market's assigned `Ti`, the SAME fresh book the decision screen
uses, classified into:

```
STILL_REJECTED_AT_TI | NOW_BROAD_AT_TI | NOW_ONE_SIDED | NOW_CLOSED |
INVALID_CLOCK_OR_DATA
```

Reported as:

```
AUDIT_N, AUDIT_VALID_N, REJECT_TO_BROAD_COUNT, REJECT_TO_BROAD_RATE,
REJECT_TO_ONE_SIDED_RATE, REJECT_TO_CLOSED_RATE
```

`REJECT_TO_BROAD_RATE` is a **routing false-negative estimate within the
audited rejected frame**. It is not `TRUE_BOARD_ELIGIBILITY`.

**V1 does not change from this audit.** A material miss rate motivates V2 — a
wider routing threshold, periodic revisit, or a faster router signal — and V2
gets its own freeze.

## CL-19. The audit is interleaved, so it measures the rule and not the clock

Audit markets are NOT appended to the end of the scan. Every one of them would
then have had the maximum time to tighten, and the measured false-negative rate
would be scan latency wearing the rule's name.

`audit_schedule()` orders both lanes by the same salted hash, so an audit
market's position is fixed before any result exists and is uncorrelated with
anything about the market. A test asserts audit reads land in both the first
and the last third of the scan.

Every row records:

```
STAGE1_CAPTURE_TIME, STAGE2_DECISION_TIME, ELAPSED_STAGE1_TO_STAGE2_SECONDS
```

and the miss rate is stratified by elapsed-time band, which separates:

```
ROUTING_RULE_ERROR     the 5-tick cut was wrong for this market
SCAN_LATENCY_EFFECT    the market simply had an hour to change
```

## CL-20. Missingness is a reason code, not a silent number

```
LAST_TRADE_SET_TIME_PRESENT / _MISSING / _INVALID_FUTURE
```

A missing timestamp never becomes `0 minutes old` or `infinite age`. Missing or
invalid fails the activity criterion **with its own code**:

```
FAIL_CLOSED > FAIL_NO_TWO_SIDED_BOOK > FAIL_SPREAD >
FAIL_NO_TRADE_TIMESTAMP > FAIL_INVALID_CLOCK > FAIL_STALE_TRADE
```

All applicable codes are recorded; `PRIMARY_FAIL_REASON` is the first in that
fixed priority order, so two runs over the same row always agree. A market that
never traded and one that traded two days ago fail the same tier for completely
different reasons, and a screen that cannot tell them apart cannot be debugged.

## CL-21. Opportunity count is not independent capacity

```
MARKET_LEVEL_GROSS_CAPACITY    eligible markets
EVENT_LEVEL_NET_CAPACITY       distinct eligible events
MAX_EVENT_EXPOSURE             largest eligible market count in one event
CORRELATED_MARKET_COUNT_PER_EVENT
EVENT_INDEPENDENCE_INFERRED_FROM_DIFFERING_SLUGS = False
```

Thirty eligible props on one NFL game are one event's worth of correlated
inventory. Capacity stays a COUNT; turning it into money needs the fill and
recycling rates that remain unmeasured.
