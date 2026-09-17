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

## Where the opportunities actually are

| market class | markets | identity-eligible | share | contests |
|---|---:|---:|---:|---:|
| FUTURE | 14,873 | 0 | 0.000 | 0 |
| PROP | 2,881 | 0 | 0.000 | 0 |
| **SPREAD** | **1,470** | **1,470** | **1.000** | **48** |
| TOTAL | 651 | 0 | 0.000 | 0 |
| **MONEYLINE** | **87** | **87** | **1.000** | **87** |
| DRAWABLE_OUTCOME | 34 | 0 | 0.000 | 0 |

**This is the finding management should take away.** Contest identity is
available on exactly two market classes — spreads and moneylines — and on
nothing else. Three-quarters of the board is season futures, and props and
totals carry no team binding on the row at all.

That is not a defect to fix by loosening identity. It is a statement about
where a contest-identified, event-risk-managed system can operate at all:
roughly 1,557 markets across 87 contests, not 20,000.

By time to kickoff, 679 identity-eligible markets are live or started and a
further ~360 are inside six hours — so the addressable set refreshes
continuously rather than sitting in one daily batch.

## Volume as scenarios — **SCENARIO, NOT MEASURED BETTOR PERFORMANCE**

Fill probability is `NOT_IDENTIFIED` for BETTOR. The whale completion rate is
**forbidden** as a substitute: it measures whether somebody else's counterparty
turned up, on another venue, for trades they chose to open. So volume can only
be shown as a grid.

At a $25 clip, assuming one order per eligible market per day:

| hypothetical p_fill | orders/day | fills/day | gross notional/day | capital turns/day |
|---:|---:|---:|---:|---:|
| 0.05 | 1,557 | 78 | $1,946 | 12 |
| 0.10 | 1,557 | 156 | $3,893 | 12 |
| 0.20 | 1,557 | 311 | $7,785 | 12 |
| 0.30 | 1,557 | 467 | $11,678 | 12 |
| 0.40 | 1,557 | 623 | $15,570 | 12 |
| 0.50 | 1,557 | 779 | $19,463 | 12 |

**Two honest caveats on that table, both load-bearing.**

First, "1,557 orders/day" is not a measured order rate. It is the count of
identity-eligible two-sided markets on one board snapshot, used as a stand-in
for one order per market per day. Treat it as an order of magnitude, not a
throughput measurement.

Second — and this is the most useful thing in the grid — **capital turns per day
is 12 in every row.** It does not move with fill probability at all. Turns are
`24 / holding-hours`; a higher fill rate raises both the money deployed and the
money recycled, and they cancel. **Turnover is a function of how long we hold,
not of how often we fill.** If management wants more turns, the lever is exit
speed, not order count.

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
   for one market, and not for 1,557.
2. **BETTOR's own fill evidence.** One real resting order produces the first
   `P_FILL` observation that is actually ours. Everything in the scenario grid
   collapses to a single column the day that exists.
3. **Authorized risk limits.** Fifteen gates are built and every one reads
   `NOT_SET`, which blocks. These are management's numbers.
4. **Exit speed**, if turns are the goal — per the table above, not order count.

## What this does not establish

- `POSITIVE_EV_OPPORTUNITY_DENSITY = NOT_IDENTIFIED`
- `ACTUAL_FILLS_PER_MINUTE = NOT_IDENTIFIED`
- `GROSS_NOTIONAL_PER_DAY = NOT_IDENTIFIED` (measured; the grid figures are scenarios)
- `REALIZED_MAKER_ECONOMICS = NOT_ESTABLISHED`
- `CAN_BETTOR_BE_HIGH_VOLUME_AT_THE_SAME_EV_STANDARD = NOT_IDENTIFIED`

The correct description: *we now know how wide the mouth of the funnel is and
which market classes it draws from; we still cannot see the exit.*

---

**A note on section 5 of the brief.** The instruction arrived truncated — the
heading "OPPORTUNITY-DENSITY BY MARKET CLASS" was present, its body was not. I
implemented what the heading states, on the venue's own axes: market class,
league, and time to kickoff. No new taxonomy was invented, and the output
declares that it was built from a truncated instruction. If a different
breakdown was intended, that section is the one to re-point.
