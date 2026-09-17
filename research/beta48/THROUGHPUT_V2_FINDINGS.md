# THROUGHPUT V2 — VOLUME AS A RATE, NOT A MARKET CENSUS

Plain English. Nothing here is a profitability claim, a forecast, or a change to
what BETTOR is allowed to trade.

*No order placed. No capital deployed. No credential used. `mirror_live = false`.
Every measured number below comes from sealed public evidence already on disk.
No venue was contacted.*

---

## The correction that prompted this

V1 reported **2,204 markets × $25 = $55,100** and called it an arithmetic daily
ceiling. That figure is a ceiling only if fills are capped at one per market per
day. **No such rule exists in this system, and none is proposed.** A market whose
book moves twenty times can be quoted twenty times; a quote that fills can be
requoted. The assumption was mine, not the system's, and describing its
consequence as a bound was wrong.

The figure is now `SINGLE_PASS_ONE_FILL_PER_MARKET_NOTIONAL` — the notional of
one sweep of the board at one clip. It is a unit of measure. It bounds nothing.
`MAX_DAILY_GROSS_NOTIONAL` reads `NOT_IDENTIFIED`, and a test pins the module so
it cannot emit a number there.

## What an opportunity is, and why polling cannot inflate it

The obvious replacement for a market census is "count the opportunities", and
the obvious way to get that wrong is to count observations. Poll twice as fast,
report twice the volume, and the chart goes up while nothing changed.

So the rule is: **an opportunity is a change in the decision surface, not an
observation.** Two consecutive observations of an identical book are one state.
The count runs over *transitions between distinct states*, so looking more often
can only discover the same transitions sooner.

This is enforced three ways rather than promised:

- `decision_state()` reads only fields a decision could use — prices, sizes, the
  ladder, market state, and the venue's cumulative traded volume. Receipt times,
  sequence numbers and latencies are structurally excluded.
- A trigger firing on an identical decision state raises `AssertionError`.
- `poll_invariance_check` decimates the real sealed series and confirms the
  count is monotone non-increasing: 150 → 146 → 128 → 102 as we look 1×, 2×, 3×,
  5× less often. Looking less often misses transitions. Looking more often never
  creates one.

One case forced a genuine design change. A trade can occur and leave the book
looking identical — somebody lifts the offer and it refills at the same price and
size. The book is unchanged; the *flow information* is new. So the venue's own
cumulative `SHARES_TRADED` counter is part of the decision state. It is monotone,
so this cannot break poll invariance.

## The tier ladder — and the biggest number in this document

Whether a change is "economically meaningful" depends on a decision model, and
BETTOR does not have one. Picking a threshold and reporting one number would
hide that. So every tier is computed:

| tier | what counts | per market-hour | total |
|---|---|---:|---:|
| **TIER 1** | touch price, market state, or a trade | **0.70** | 5 |
| **TIER 2** | + size at the touch *(declared default)* | **20.88** | 150 |
| **TIER 3** | + any change deeper in the ladder | **25.61** | 184 |
| VENUE CEILING | the venue's own book-version stamp | 29.09 | 209 |

**Tier 1 and tier 3 differ by 37×.** Every downstream volume figure inherits that
choice, so it is declared rather than defaulted silently.

Read the trigger counts and the reason is stark: across 7.18 market-hours, the
**best bid never moved once**, the best ask moved once, and four trades printed.
Essentially all the state change is size and depth reshuffling at a frozen touch.

TIER 2 is the default because a resting maker's queue position and adverse
selection genuinely depend on the size queued at its own price. A reshuffle five
levels deep at a price we would never rest at is not a re-decide. That is a
judgement, it is written down, and the other tiers sit beside it.

## The measured arrival, and what it does not cover

```
MARKETS_OBSERVED                                6      (of which addressable: 2)
OBSERVATIONS                                1,365
MARKET_HOURS_OBSERVED                        7.18
DISTINCT_QUOTE_OPPORTUNITIES                  150
  PER_HOUR                                 124.28
  PER_MARKET_HOUR                           20.88
  PER_EVENT_HOUR                     NOT_IDENTIFIED   (no identity on this run)

MEDIAN_OPPORTUNITIES_PER_MARKET              23.5
P75 / P90 / P95                       32.8 / 36.0 / 37.0
MARKETS_WITH_0 / 1 / 2-5 / 6-20 / >20     0 / 0 / 0 / 3 / 3
```

By family: SPREAD 31.5 per market-hour (1 market), TOTAL 28.2 (1 market), FUTURE
16.4 (4 markets). **No moneyline was in the sample.**

Two integrity checks came out the right way. Every observable change we counted
was accompanied by a move in the venue's own book version — `OBSERVABLE_CHANGE_
WITHOUT_VENUE_VERSION_MOVE = 0`, so we never invented an opportunity the venue
did not have. And the venue's version advanced 25 times with every field we
capture identical: real observation loss, measured rather than assumed absent.

**The honest limits, and they are severe.** This rests on six markets for 72
minutes, of which **two** are in the addressable universe. It is one time of day,
one sport, one venue state. The source run was also rate-limited hard — 5,835 of
7,200 attempts returned HTTP 429 — which is why only six markets have series at
all. Carrying 20.88 per market-hour to 2,204 markets is an assumption, not a
measurement, and it is the single largest piece of load-bearing extrapolation in
this document.

The direction of the remaining bias is `UNDERCOUNT`: fair-value change, inventory
change, risk-state change, and our own quote filling or expiring are all real
triggers for the mature engine and **none** is observable in a public book tick.

## Many cycles per market

`UNIQUE_MARKETS_TRADED`, `ORDER_INTENTS`, `FILLED_ORDERS` and `FILLED_NOTIONAL`
are four different sizes of the same day, and V1 used the first as though it were
the third. They are now kept apart, with the multipliers between them shown, and
three lifecycle paths modelled explicitly: quote → cancel → requote; quote → fill
→ inventory action → requote; quote → partial fill → rest/cancel/requote.

`FILLS_PER_MARKET_CAP = NOT_IDENTIFIED`, because no rule exists to read.

## Anti-churn — the other half of the mandate

Allowing many cycles per market makes order count free unless something stops it.
Four counters, all of which must read zero:

```
REQUOTE_WITHOUT_MATERIAL_STATE_CHANGE      0
DUPLICATE_ECONOMIC_INTENT                  0
ROUND_TRIP_WITHOUT_POSITIVE_EXPECTED_EV    0
ORDERS_CREATED_ONLY_BY_POLL_FREQUENCY      0
```

A round trip whose EV is `NOT_IDENTIFIED` counts as churn, not as a pass —
absence of an EV is not evidence of a good one. The economic identity of an
intent is (market, side, price, size); ids, retry counters and timestamps are
excluded, because including them would make every duplicate look distinct.

**Scope, stated plainly:** these are properties of the design. No live engine has
produced a single intent, so a clean reading here is not evidence about
production behaviour.

## Execution units are not risk units

The totals work made this unavoidable. 647 totals raised addressable markets by
41.6% and canonical events by zero — they landed on 41 contests, about sixteen
alternate lines each.

Sixteen lines on one football match are **sixteen chances to execute and one
thing to be wrong about.** Only the first number scales throughput; only the
second governs exposure. Per event the model now reports markets, active quotes,
gross notional, net directional exposure, correlated exposure and capital
occupied, plus the execution-to-risk-unit ratio.

Correlated exposure is the sum of *absolute* exposures within an event — the
fully-correlated assumption — because `CORRELATION_COEFFICIENT_STATUS =
NOT_IDENTIFIED` and over-stating concentration is the safe direction.

## The volume equation

```
OPPORTUNITY_ARRIVAL  x  PCT_PASSING_EV  x  PCT_PASSING_RISK  x  P_FILL
                     x  AVERAGE_FILLED_SIZE  =  EXPECTED_EXECUTED_NOTIONAL
```

Three of the five terms are `NOT_IDENTIFIED`. They stay separately visible and
are never collapsed: an unknown factor is not a zero and not a one, so a product
containing one is `NOT_IDENTIFIED` and `MISSING_TERMS` names which.

## Backsolve — **SCENARIO, NOT PREDICTED PERFORMANCE**

Capacity planning: what the machine would have to do, never what it will do.
At p_fill 0.20 and a 2-hour holding time:

| monthly target | clip | gross/day | filled orders/day | order intents/day | capital |
|---:|---:|---:|---:|---:|---:|
| $5M | $25 | $166,667 | 6,667 | 33,333 | $13,889 |
| $5M | $250 | $166,667 | 667 | 3,333 | $13,889 |
| $10M | $25 | $333,333 | 13,333 | 66,667 | $27,778 |
| $10M | $250 | $333,333 | 1,333 | 6,667 | $27,778 |
| **$20M** | **$25** | **$666,667** | **26,667** | **133,333** | **$55,556** |
| **$20M** | **$250** | **$666,667** | **2,667** | **13,333** | **$55,556** |
| $50M | $25 | $1,666,667 | 66,667 | 333,333 | $138,889 |
| $50M | $250 | $1,666,667 | 6,667 | 33,333 | $138,889 |

**Two things management should take from this table.**

First, **capital is not the constraint.** $20M a month at a two-hour holding time
needs about **$55,600** of working capital, because it turns twelve times a day.
Capital scales with notional and holding time, not with clip size.

Second, **clip size is the dominant operational lever.** The same $20M needs
26,667 fills a day at $25 or 2,667 at $250. Order throughput is what gets hard,
and a 10× clip makes it 10× easier — subject entirely to whether depth supports
it, which is `NOT_IDENTIFIED` (the board's quote fields carry a price and no
size).

For scale against the measured supply, and **carrying the 2-market rate to 2,204
markets, which is the assumption to distrust**: at 12 tradable hours a day the
addressable universe would throw off roughly 552,000 tier-2 state changes daily.
$20M/month at a $250 clip would need 13,333 intents — about **2.4%** of that
supply passing EV and risk. At a $25 clip, about 24%.

## The critical question

> Does the BETTOR market universe generate enough distinct, economically
> meaningful state changes per day that — if a reasonable fraction pass EV and
> fill — $20M+ monthly executed volume is operationally plausible?

**`NOT_IDENTIFIED`.**

Not YES: the arrival rate is real, but it was measured on two addressable markets
for 72 minutes, and two of the five equation terms do not exist. The 2.4% figure
above is arithmetic on an extrapolation, not evidence.

Not NO: nothing measured rules the volume out. The observed state-change supply
is not obviously too small, so NO would be exactly as unevidenced as YES.

What would settle it: an arrival rate measured across a representative sample of
the addressable universe; a measured tradable-hours-per-day figure; a first fair
value, giving `PCT_PASSING_EV`; one real resting order, giving `P_FILL`.

**2,204 addressable markets is not an answer to this question.** It is a census,
and the question is about a rate. Inferring YES from it would repeat the V1
defect exactly.

## Fair value — the central economic blocker

`FAIR_VALUE_CURRENT_STATUS = NOT_IDENTIFIED`, and no model was invented to clear
the field. One written to clear it would make every downstream EV look measured
while being assumed.

**Already available:** sealed board snapshots with venue best bid and ask; sealed
book tick series with top-of-book, ladder and traded-volume deltas; canonical
event identity across all three market families; settled outcomes in the snapshot
log; the venue's fee coefficient and tick size per market.

**Not yet available:** an independent outcome model or external probability
source; a prospective timestamped prediction record that predates the outcome it
is scored against; BETTOR-native fill evidence.

**Shortest path to a first prospective fair value:**

1. Pick ONE narrow, high-frequency market family with settled history already on
   disk. The natural candidate is **totals**, now that 647 carry canonical event
   identity.
2. Freeze a prediction rule **before** seeing outcomes; stamp every prediction
   with the time it was made.
3. Run it prospectively against the venue mid on new contests only, scoring
   calibration and log loss, with no refitting.
4. Publish the calibration record. Only if it beats the mid out of sample does a
   fair value exist at all.
5. Then, and only then, `PCT_PASSING_EV` becomes measurable.

`ESTIMATED_CALENDAR_TIME = NOT_IDENTIFIED` — step 3 runs for as long as new
contests take to settle, and how many are needed depends on an effect size nobody
has measured.

Throughput capacity and edge are different questions. No amount of throughput
work moves this field, and a volume target never loosens the EV gate: if a target
needs more orders than positive EV supplies, the answer is fewer orders.
