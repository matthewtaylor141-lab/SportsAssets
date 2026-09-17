# THROUGHPUT V1 — CAN BETTOR BE HIGH-VOLUME WITHOUT LOWERING ITS EV STANDARD?

Plain English. Nothing here is a profitability claim, and nothing here changes
what BETTOR is allowed to trade.

*No order placed. No capital deployed. No credential used. `mirror_live = false`.
Every number below comes from sealed public evidence already on disk — the
20,000-row board capture and the 1,365-row tick capture. No venue was contacted.*

---

## The short answer

**The opportunity side is not the constraint. The EV side is, and it is not
measurable yet — so the honest answer to the question as asked is
`NOT_IDENTIFIED`, and it will stay that way until BETTOR has its own fair value
and its own fill evidence.**

What we *can* now say precisely is where the funnel narrows, and it narrows in a
place worth knowing about.

## The funnel, measured

```
BOARD_MARKETS                  20000
CANONICAL_EVENTS                  87
IDENTITY_ELIGIBLE_MARKETS       1557     7.8% of the board
ACTIVE_TRADABLE_MARKETS         1545
BOOKS_OBSERVED                  1557
EV_EVALUATIONS                    12     (the dry-run rehearsal)
MAKER_CANDIDATES                  12
TAKER_CANDIDATES                   0     scope: passive-maker only
NO_TRADE_DECISIONS                12
POSITIVE_EV_MAKER_CANDIDATES   NOT_IDENTIFIED
POSITIVE_EV_TAKER_CANDIDATES   NOT_IDENTIFIED
RISK_ADMISSIBLE_CANDIDATES         0     because every limit is NOT_SET
WOULD_QUOTE / WOULD_TAKE       0 / 0
WOULD_NOT_TRADE                   12
```

Two of those numbers are absent rather than zero, and the distinction is the
whole point. `POSITIVE_EV_*` is `NOT_IDENTIFIED` because a positive-EV count
requires an EV, and EV requires a fair value and a BETTOR-native fill
probability. We have neither. A zero there would claim we looked and found
none; we cannot look.

`RISK_ADMISSIBLE = 0` is different again — that is a real zero, and it counts
*authorization*, not opportunity. Every risk limit reads `NOT_SET`, and
`NOT_SET` blocks.

`IDENTITY_ELIGIBLE_MARKETS = 1,557` counts only rows that *establish* a contest
identity of their own. The addressable universe is larger — 2,204 — because 647
totals attach to those identities. The next section separates the two.

## The addressable universe, now that totals bind

The totals resolver moved 647 markets from unaddressable to addressable. It did
this by ATTACHING them to contests that moneyline and spread rows had already
established — it cannot mint a contest, and a board of totals alone still
resolves to nothing.

```
MONEYLINE_MARKETS                                    87
SPREAD_MARKETS                                    1,470
BOUND_TOTAL_MARKETS                                 647
TOTAL_CANONICALLY_ADDRESSABLE_SPORTS_MARKETS      2,204     11.0% of the board
                                                            (was 1,557, +41.6%)
UNBOUND_TOTAL_MARKETS                                 4     they stay unbound
AMBIGUOUS_TOTAL_MARKETS                               0

CANONICAL_EVENTS                                     87     unchanged
EVENTS_WITH_MONEYLINE                                87
EVENTS_WITH_SPREAD                                   48
EVENTS_WITH_TOTAL                                    41
EVENTS_WITH_ALL_THREE                                41
```

No market is counted in two families: a venue row carries exactly one
`sportsMarketTypeV2`, so the 2,204 is a sum over disjoint sets, and the code
computes the overlap rather than assuming it is empty.

**The number that matters here is the one that did not move.** Canonical events
stayed at 87. The 647 totals land on 41 contests — about sixteen alternate
lines per contest — and every one of those 41 already had both a moneyline and
a spread. So the addressable set grew 42% in *markets* and 0% in *independent
contests*.

That distinction is load-bearing for anything downstream that manages event
risk. Sixteen total lines on one football match are sixteen opportunities to
quote and one thing to be wrong about. Counting them as sixteen independent
positive-EV opportunities would be the same error as counting a quote update as
a trade — it inflates a number by redefining its unit.

2,121 of the 2,204 carry both a best bid and a best ask on the snapshot (every
one of the 647 totals does). That is two-sidedness, not depth: the board's quote
fields carry a price and no size, so the notional a market could absorb is
`NOT_IDENTIFIED` from this evidence.

## Where the opportunities actually are

| market class | markets | identity-eligible | share | contests |
|---|---:|---:|---:|---:|
| FUTURE | 14,873 | 0 | 0.000 | 0 |
| PROP | 2,881 | 0 | 0.000 | 0 |
| **SPREAD** | **1,470** | **1,470** | **1.000** | **48** |
| **TOTAL** | **651** | **647** (attached) | **0.994** | **41** |
| **MONEYLINE** | **87** | **87** | **1.000** | **87** |
| DRAWABLE_OUTCOME | 34 | 0 | 0.000 | 0 |

The `identity-eligible` column means two different things by row, and the
difference is the architectural condition. Spreads and moneylines carry two
venue team ids on the row, so they *establish* identity. Totals carry none —
their 647 are **attached** to identities those rows already created, and the
four that cannot name a contest stay unbound.

**This is the finding management should take away.** Contest identity is
established on exactly two market classes — spreads and moneylines. Everything
else either attaches to one of those (totals) or has nothing to attach to at
all. Three-quarters of the board is season futures; props carry no team binding
on the row.

That is not a defect to fix by loosening identity. It is a statement about
where a contest-identified, event-risk-managed system can operate at all:
roughly 2,204 markets across 87 contests, not 20,000.

By time to kickoff, 679 identity-eligible markets are live or started and a
further ~360 are inside six hours — so the addressable set refreshes
continuously rather than sitting in one daily batch.

## Turnover, in three separate concepts

**A correction first, because the earlier version of this page got it wrong.**
It said: *"turnover is a function of how long we hold, not of how often we
fill."* That sentence is wrong as written. It is true only of the capital-turns
*ratio*, where a higher fill rate raises the money deployed and the money
recycled in the same proportion so the fill rate cancels. It is false of
executed turnover, which scales **directly** with fill rate: filling twice as
often at the same clip trades twice the notional. The page now keeps three
things apart that the old sentence collapsed into one.

| | what it measures | how fill rate moves it |
|---|---|---|
| **A — opportunity throughput** | candidate opportunities per unit time | not at all |
| **B — executed turnover** | filled notional per unit time | **directly, one for one** |
| **C — capital velocity** | filled notional per capital dollar per unit time | cancels from the ratio |

Four inputs are now modelled separately, because conflating any two of them is
how the wrong sentence got written: opportunity arrival rate, fill probability,
average filled notional, capital occupancy time.

### The grid — **SCENARIO, NOT MEASURED BETTOR PERFORMANCE**

Fill probability is `NOT_IDENTIFIED` for BETTOR. The whale completion rate is
**forbidden** as a substitute: it measures whether somebody else's counterparty
turned up, on another venue, for trades they chose to open. Every row below is
a hypothetical.

At a $25 clip and a two-hour holding time, one order per addressable market per
day (2,204 — the universe with totals in it):

| hypothetical p_fill | intents/day (A) | fills/day (B) | gross filled notional/day (B) | avg capital occupied | turns/day (C) |
|---:|---:|---:|---:|---:|---:|
| 0.05 | 2,204 | 110 | $2,755 | $230 | 12 |
| 0.10 | 2,204 | 220 | $5,510 | $459 | 12 |
| 0.20 | 2,204 | 441 | $11,020 | $918 | 12 |
| 0.30 | 2,204 | 661 | $16,530 | $1,378 | 12 |
| 0.40 | 2,204 | 882 | $22,040 | $1,837 | 12 |
| 0.50 | 2,204 | 1,102 | $27,550 | $2,296 | 12 |

Read the columns as three different questions. Column A does not move — the
board offers what it offers regardless of who fills us. Columns B rise tenfold
across the grid — **that is the fill rate doing exactly what the old sentence
denied.** The turns column is flat at 12 because it is B divided by the capital
that B itself commits, and `24 / 2 hours = 12` whatever the fill rate. Halve the
holding time and every row reads 24; that lever is exit speed, and it is a
different lever from the one in column B.

So both levers are real and they do different jobs: **fill rate sets how much
gets traded; holding time sets how fast the capital behind it comes back.**

`PEAK_CAPITAL_OCCUPIED` is `NOT_IDENTIFIED` and deliberately so — a peak needs
an arrival-time distribution across the day, and BETTOR has measured none. The
model emits a true upper bound instead (every fill of the day open at once) and
labels it a bound, not an estimate.

`EXPECTED_NET_EV_PER_CAPITAL_DOLLAR_PER_DAY` is `NOT_IDENTIFIED` in every row,
because it needs a net EV per filled order and BETTOR has no fair value. That
is the same wall the funnel hits, and it is the wall that matters.

**One caveat on column A.** "2,204 intents/day" is not a measured order rate. It
is the count of canonically addressable markets on one board snapshot, used as a
stand-in for one order per market per day. Treat it as an order of magnitude.

## The objective for the mature engine

**HIGH THROUGHPUT SUBJECT TO POSITIVE NET EV.** The subject-to clause is the
whole objective, not a qualifier on it. The engine should seek many *independent*
positive-EV opportunities, high executable fill throughput, short capital
occupancy and rapid capital recycling — and volume must never make a negative or
unidentified-EV order admissible.

That constraint is enforced by a function, not by intention:
`admissible_under_objective` takes the throughput gain as an argument purely so
that a test can prove it is ignored. Anything that is not an explicit positive
EV — including `NOT_IDENTIFIED` — refuses, whatever throughput is offered.

Three of the objective's four components cannot be scored today, so
`OBJECTIVE_ACHIEVED = NOT_IDENTIFIED`. Scoring it on the one measurable
component would be the flattering answer.

Note also what "independent" does here. The totals work grew the market count by
42% and the independent-contest count by zero, so it advanced the *throughput*
half of the objective and not the *independence* half at all.

## The capital metric, and the guardrail on it

`EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR_PER_HOUR` is now computable wherever
the inputs exist, and it ranks opportunities by how hard each dollar works
rather than by ticket size. A small fast-recycling opportunity can legitimately
outrank a large slow one.

It cannot do the other thing. An opportunity with negative or unidentified EV
is **not ranked at all** — it goes into a separate list rather than to the
bottom of the ranking, because an inadmissible opportunity sitting at the bottom
of a ranked list still looks ranked. Turnover is a tie-breaker among trades that
have already cleared the bar; it is never a route across it.

## What would actually unlock volume

In order of what binds first:

1. **An independent fair value.** Until BETTOR has one, every EV is
   `NOT_IDENTIFIED` and the funnel cannot progress past `EV_EVALUATIONS` — not
   for one market, and not for 2,204.
2. **BETTOR's own fill evidence.** One real resting order produces the first
   `P_FILL` observation that is actually ours. Everything in the scenario grid
   collapses to a single column the day that exists — and that column sets
   executed turnover directly.
3. **Authorized risk limits.** Fifteen gates are built and every one reads
   `NOT_SET`, which blocks. These are management's numbers.
4. **Exit speed**, if capital *velocity* is the goal. This is a different lever
   from item 2 and the two should not be traded off against each other.

## What this does not establish

- `POSITIVE_EV_OPPORTUNITY_DENSITY = NOT_IDENTIFIED`
- `ACTUAL_FILLS_PER_MINUTE = NOT_IDENTIFIED`
- `GROSS_NOTIONAL_PER_DAY = NOT_IDENTIFIED` (measured; the grid figures are scenarios)
- `REALIZED_MAKER_ECONOMICS = NOT_ESTABLISHED`
- `CAN_BETTOR_BE_HIGH_VOLUME_AT_THE_SAME_EV_STANDARD = NOT_IDENTIFIED`
- `MEASURED_BETTOR_P_FILL = NOT_IDENTIFIED`
- `PEAK_CAPITAL_OCCUPIED = NOT_IDENTIFIED` (an upper bound only)
- `MARKET_DEPTH_PER_ADDRESSABLE_MARKET = NOT_IDENTIFIED` (the board's quote
  fields carry a price and no size)
- `WHALE_DAILY_TURNOVER_REFERENCE = NOT_IDENTIFIED` — the only turnover figure
  in the sealed evidence is a **lifetime** $249M for SwissTony, an account
  formally held out of the reference figures. No time basis, and an excluded
  source: two independent reasons it cannot set a daily whale-scale benchmark.

The correct description: *we now know how wide the mouth of the funnel is and
which market classes it draws from; we still cannot see the exit.*

---

**A note on section 5 of the brief.** The instruction arrived truncated — the
heading "OPPORTUNITY-DENSITY BY MARKET CLASS" was present, its body was not. I
implemented what the heading states, on the venue's own axes: market class,
league, and time to kickoff. No new taxonomy was invented, and the output
declares that it was built from a truncated instruction. If a different
breakdown was intended, that section is the one to re-point.
