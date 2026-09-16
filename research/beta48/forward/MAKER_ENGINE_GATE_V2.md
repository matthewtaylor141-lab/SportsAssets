# MAKER_ENGINE_GATE_V2

Issued after the frozen Phase-A experiments: the event-identity validation, the
salted-hash replication, the observed-prefix generalization result and the
stage-1 rolling census. Supersedes `DECISION_REPORT_S10.md`, which is retained
unedited as the v1 record.

```
MAKER_ENGINE_GATE_V2 = BLOCKED
```

Not FAIL: nothing here establishes that a maker engine loses money. Not PASS:
the terms that would decide it are still unmeasured, and one of them cannot be
measured without a BETTOR order. **A BLOCKED verdict with a precisely named
blocker and a costed next experiment is a completed sprint result, and it is
the honest one.**

---

## THE GATE BOARD

```
OBSERVED_PREFIX_SCOPE          20,000 markets, one capture instant, NOT the
                               whole board. DISCOVERY_LIST_EXHAUSTED = NO,
                               TRUE_ACTIVE_BOARD_SIZE = NOT_IDENTIFIED.

MARKET_WEIGHTED_RESULT         STAGE1_BROAD_SURVIVORS 9,182 / 20,000 = 45.9%
EVENT_WEIGHTED_RESULT_IF_VALID NOT_IDENTIFIED -- the event key failed
                               validation; event-weighted inference is OFF and
                               raises rather than returning a number.

ELIGIBLE_MARKETS               9,182  (stage 1 only; stage 2 never run at scale)
ELIGIBLE_INDEPENDENT_EVENTS    NOT_IDENTIFIED
                               lower bound 444 families, upper bound 9,182
                               (a CEILING, not an estimate)

TRADE_ACTIVITY                 SEG 10: 10 trade intervals / 897 pairs = 1.11%
                               = one trade per 44.9 market-minutes.
                               16 of 23 markets traded ZERO times in 19.5 min.
                               SEG 8 replication: 1.92%, one per 26.0 min.
                               LEVEL_B (count/elapsed), not recency.

QUEUE_AHEAD                    BID  median 107.04 contracts = 1.32 median
                                    trades -> 59.2 min to the front if EVERY
                                    trade hits the bid, 118.3 min at 50/50
                               ASK  median 101.96 = 1.26 trades -> 56.4 / 112.7

SPREAD_CAPTURE                 $0.0100 per contract at a 1-tick spread
                               (median spread is 1 tick: 613 of 780 pairs)

MAKER_REBATE                   $0.0053 per contract, both sides, at p=0.305
                               $0.00 at a one-contract clip -- the floor is
                               U-shaped in price: 41 contracts at p=0.01,
                               5 at p=0.10, 2 at p=0.30-0.70, 41 at p=0.99

VERIFIED_INCENTIVES            liquidityProgram, VERIFIED_FROM_CAPTURE on the
                               panel: REWARD_POOL 10,000 / 1,250 / 500,
                               TARGET_SIZE 20,000 / 1,000, DISCOUNT_FACTOR
                               0.30 / 0.35, periods live / day_of / early.
                               ALL 23 panel markets are UFC.
                               TRUE_ACTIVE_INCENTIVE_UNIVERSE_SIZE =
                               NOT_IDENTIFIED (pagination did not advance).
                               NON_UFC_ACTIVE_INCENTIVES = NOT_OBSERVED,
                               which is NOT the same as PROVEN_ABSENT.

COUNTERFACTUAL_FILL_EVIDENCE   F0-F3 bounds only, and they are unevaluated --
                               the tape gate (C-14) has not cleared, so no
                               Time & Sales fill result may be promoted.
                               ACTUAL_BETTOR_FILL_PROBABILITY = NOT_IDENTIFIED.
                               TOUCH != FILL.

ADVERSE_SELECTION              NOT_IDENTIFIED for this panel.
                               A HARD CONSTRAINT exists from a different flow:
                               -$0.0140/share making a market to RN1's takes,
                               112,553 trades over 9,337 conditions, fills
                               OBSERVED not modelled, net -$0.0090/share,
                               95% CI [-0.0143,-0.0038], clustered by
                               condition. See the scope warning below.

RESIDUAL_INVENTORY_COST        NOT_IDENTIFIED.
                               UNPAIRED_LEG_EXIT_COST = NOT_IDENTIFIED, and no
                               reference account supplies it because none of
                               the six sell (0.00%-0.54% of lot stake).

CAPITAL_OCCUPANCY              BOUNDED BELOW at ~1-2 hours per side to reach
                               the front of the queue, before any fill.

TRADING_NET_EX_INCENTIVES      NOT_IDENTIFIED
TOTAL_NET                      NOT_IDENTIFIED

BIGGEST_REMAINING_UNKNOWN      ACTUAL_BETTOR_PASSIVE_FILL_PROBABILITY
SHORTEST_NEXT_EXPERIMENT       see below -- two, and the cheaper one is free
```

`TRADING_NET_EX_INCENTIVES` and `TOTAL_NET` are `NOT_IDENTIFIED` and not
numbers, because three of their terms are. **A sum containing an unmeasured
term is not a smaller number; it is not a number.**

---

## WHAT THE SALTED-HASH PANEL ACTUALLY ANSWERED

It was dispatched to answer: *is a one-tick spread, a static touch and a
sub-2% trade rate a fact about the BOARD, or a fact about one UFC card?*

```
PANEL_SELECTION_METHOD      SALTED_HASH_WITHIN_FROZEN_STRATA
PANEL_SALT                  BETA48-FORWARD-PANEL-2026-09-16
SALT_FROZEN_BEFORE_OUTCOMES YES
OUTCOME_LEAKAGE             NO
SPORT_DISTRIBUTION          ufc 23 / 23 = 100.0%
SALTED_PANEL_GENERALIZATION UFC_ONLY
```

**The hash mechanism replicated. The generalization did not, and the reason is
structural.** A different, deterministic, pre-committed draw produced 23 markets
across two fight cards instead of 16 across one — and still 100% UFC, because
the stratification universe is *markets carrying an active liquidity
programme*, and on the observed prefix that set is entirely UFC. No selection
rule can draw a non-UFC market out of a frame that contains none.

So this is a **within-UFC replication of the hash mechanism**, not a board
cross-section, and it is labelled as such rather than quietly promoted.

### The replication result, reported whether or not it helps

```
                          SEG 8 (lexicographic)   SEG 10 (salted hash)
CONSECUTIVE_PAIRS                624                    897
UNIQUE_MARKETS                    16                     23
TOUCH_PRICE_MOVED               0.16%                  1.00%
ANY_TRADE_IN_INTERVAL           1.92%                  1.11%
MINUTES_PER_TRADE_PER_MARKET     26.0                   44.9
MEDIAN_TRADE_SIZE               52.32                  81.22
BEST_BID_SIZE_MEDIAN           204.41                 107.04
BEST_ASK_SIZE_MEDIAN            68.00                 101.96
MEDIAN_SPREAD                  1 tick                 1 tick
MARKETS_WITH_ZERO_TRADES           -           16 / 23 = 69.6%

DIRECTIONALLY_CONSISTENT     YES
MATERIAL_REGIME_DIFFERENCE   NO
```

The independent replication is **worse on the term that matters most**: one
trade per 44.9 market-minutes against 26.0, and **sixteen of twenty-three
markets did not trade once** in 19.5 minutes of continuous observation each.

This is the first LEVEL_B activity figure in the programme — a count over
elapsed time from repeated `sharesTraded` deltas, not the `lastTradeSetTime`
recency proxy. The two were kept apart precisely so this measurement could
exist, and it did not flatter the maker case.

---

## THE GENERALIZATION RESULT, QUANTIFIED

```
OBSERVED_PREFIX_MARKETS   20,000
STATUS_OPEN               19,996   (100.0%)
TWO_SIDED_BBO_PRESENT     11,290   (56.5%)
STAGE1_BROAD_SURVIVORS     9,182   (45.9% of prefix, 81.3% of two-sided)

PRIMARY_FAIL_REASONS
  FAIL_NO_TWO_SIDED_BOOK   8,709
  FAIL_SPREAD              2,105
  FAIL_CLOSED_OR_UNKNOWN       4

BROAD SURVIVOR COMPOSITION, BY TYPE
  FUTURE      7,152   77.9%
  SPREAD        994   10.8%
  TOTAL         581    6.3%
  PROP          366    4.0%
  MONEYLINE      83    0.9%

BROAD SURVIVOR COMPOSITION, BY LEAGUE
  nfl         4,141   45.1%
  cfb         2,484   27.1%
  (no key)      407    4.4%
  epl           383    4.2%
  mlb           272    3.0%
  ufc           174    1.9%   <-- the entire measured microstructure
  lal           117    1.3%
```

**UFC is 1.9% of the eligible board.**

### One part of the microstructure DOES generalize, and it should be said

The stage-1 screen reads the spread from the board row, so the spread — unlike
depth and trade frequency — **is** measured board-wide:

```
SPREAD_TICKS, all 11,290 two-sided markets on the observed prefix
   1 tick   8,357   74.0%   <- the modal state of the whole board
   2 ticks    365    3.2%
   3-5 ticks  463    4.1%
   6-19       571    5.1%
   20+      1,464   13.0%
```

So "the spread is one tick" is **not** a UFC artifact — it is the modal state
of three quarters of the two-sided board, and the $0.0100 spread-capture figure
carries board-wide.

What does **not** carry is everything that needs a book read:

```
QUEUE_AHEAD          measured on UFC only  (needs L2 depth)
TRADE_FREQUENCY      measured on UFC only  (needs repeated sharesTraded)
TRADE_SIZE           measured on UFC only
ACTIVE / HIGH_ACTIVITY   NOT_IDENTIFIED board-wide
```

That distinction matters for the gate: the **reward** side of the maker
arithmetic generalizes; the **cost** side — how long capital waits, and how
often anything trades at all — is measured on 1.9% of the board and is
`NOT_IDENTIFIED` on the other 98.1%. It is not "low" there. It is unmeasured,
and a 9,182-read census would settle it for free.

```
PANEL_GENERALIZES_TO_BOARD = NO
COVERAGE_BIAS              = YES
OUTCOME_LEAKAGE            = NO
```

That is the honest generalization result: **not that the board is quiet, but
that we have measured 1.9% of it and may not speak for the rest.**

---

## THE ECONOMICS, STATED WITH ITS SCOPE

Under the venue's documented price-time priority, a quote joining at the touch
sits behind the size already there:

```
BID  107.04 ahead / 81.22 per trade = 1.32 median trades
     -> 59.2 min to the front if EVERY trade hits the bid; 118.3 min at 50/50
ASK  101.96 ahead / 81.22 per trade = 1.26 median trades
     -> 56.4 min to the front if EVERY trade hits the ask; 112.7 min at 50/50
```

So on either side a maker waits **on the order of one to two hours to reach the
front of the queue**, and then needs a further trade to be filled at all. The
reward for the completed round trip, if both sides ever fill:

```
SPREAD_CAPTURE_1_TICK       $0.0100 per contract
MAKER_REBATE_BOTH_SIDES     $0.0053 per contract  (p = 0.305)
GROSS_IF_BOTH_SIDES_FILL    $0.0153 per contract
AT THE MEDIAN TRADE SIZE    $1.24 per completed round trip
```

### The scope warning that must not be dropped

The measured adverse selection is **−$0.0140 per share**, which is 91.5% of
that $0.0153 gross. It is tempting to subtract and report a net. **That
subtraction is not licensed**, and the reason is a scope difference, not a
statistical one:

- −$0.0140 was measured **making a market to RN1's taker flow**, on 112,553
  trades across 9,337 conditions with one-hot settlements resolving strictly
  after the trade. Fills were **observed**, not modelled. It is a strong
  measurement of *that* flow.
- The $0.0153 is the gross available on a **UFC incentive panel** where nobody
  in particular is taking, and where 69.6% of markets did not trade at all.

They are different flows. Carrying the first into the second as a subtraction
would manufacture a net from two unrelated samples. So:

```
ADVERSE_SELECTION_THIS_PANEL = NOT_IDENTIFIED
ADVERSE_SELECTION_CONSTRAINT = -$0.0140/share against informed flow, and the
                               break-even rebate against it would be 1.81% of
                               notional on a 50c contract -- a schedule that
                               does not exist
```

What the constraint *does* establish is the size of the hole the unmeasured
term could occupy: an adverse-selection cost anywhere near the only figure we
have measured would consume essentially the entire gross. That is why the gate
is blocked on this term specifically and not on a general shortage of data.

### The rebate floor is a design constraint, not a rounding detail

The fee is banker-rounded to the cent **per fill**, so the maker rebate is not
linear in clip size near zero — it is zero:

```
clip     rebate    as % of a 1-tick spread
   1     $0.00       0.0%    <- a one-contract fill earns NOTHING
   2     $0.01      50.0%
   5     $0.01      20.0%
  10     $0.03      30.0%
 100     $0.26      26.0%
1000     $2.65      26.5%

MINIMUM_CLIP_FOR_NONZERO_REBATE_BY_PRICE  (U-shaped in p)
  p=0.01  41    p=0.10   5    p=0.50   2    p=0.90   5
  p=0.02  21    p=0.20   3    p=0.70   2    p=0.95   9
  p=0.05   9    p=0.30   2                  p=0.99  41
```

For a programme seeking the **smallest** defensible beta this is directly on
point: the rebate leg does not exist at a one-contract clip, and is weakest
exactly at the longshot prices where small capital goes furthest.

---

## WHY BLOCKED, UNDER THE RULE FROZEN BEFORE THE RESULT

`DECISION_REPORT_TEMPLATE.md` fixes it: while fill probability, queue position
and adverse selection are `NOT_IDENTIFIED`, the gate is **BLOCKED**.

```
ACTUAL_BETTOR_FILL_EVIDENCE = NOT_IDENTIFIED
```

Answering the gate from an invented fill probability would be worse than
leaving it unanswered. **Do not substitute conditional arithmetic for
execution evidence** — "if we filled at rate r we would earn X" is not a
result, it is a restatement of the unknown with extra steps.

### What would change the verdict to FAIL

Not this. Nothing measured establishes a loss. Two of the three blocked terms
(`ADVERSE_SELECTION`, `RESIDUAL_INVENTORY_COST`) are costs, so their true
values can only move the answer toward FAIL — but "probably bad" is not a
verdict this programme issues.

### What would change it to PASS

`ACTUAL_BETTOR_PASSIVE_FILL_RATE` and `ACTUAL_ADVERSE_SELECTION` measured on
BETTOR's own resting orders, showing a positive
`TRADING_NET_EX_INCENTIVES` — or, under MODEL B, a positive `TOTAL_NET`
**labelled as incentive-dependent**.

---

## THE NEXT EXPERIMENTS, IN ORDER OF COST

**1. The stage-2 rolling census — FREE, NO NEW AUTHORITY, DO THIS FIRST.**
9,182 book reads, 1.28 hours at 2 requests/second, public and unauthenticated.
It converts `ACTIVE` and `HIGH_ACTIVITY` from `NOT_IDENTIFIED` to measured
across the **other 98.1% of the board**, and it is the only cheap way to learn
whether the UFC microstructure is representative or pathological. The runtime
presence of every field it needs has already been verified against captured
rows. **It is the highest information-per-dollar experiment available and it
requires nothing that is not already permitted.**

**2. The public tape, once the C-14 block gate clears.** Gives
`TIME_TO_QUEUE_DEPLETION` and `MARKOUT_AFTER_COUNTERFACTUAL_MAKER_FILL` — the
adverse-selection term — on symbol-days that reconcile and carry no block
volume. Still blocked: `UNKNOWN_EXECUTION_TYPE != CLOB_EXECUTION`,
`DMR_TRADE_VOLUME_INCLUDES_BLOCK_VOLUME = NOT_IDENTIFIED`.

**3. Institutional read-only L2.** Continuous book state instead of 30-second
polling. Converts four blocked terms from NO to PROXY_ONLY and **none to YES**.
`BETTOR_GRANTED_SCOPE = NOT_IDENTIFIED`.

**4. FIX MBO.** The only path that answers the counterfactual outright — and
even then it yields `COUNTERFACTUAL_PASSIVE_FILL`, never
`ACTUAL_BETTOR_PASSIVE_FILL`.

**5. Micro-live passive orders.** The only thing that ever answers
`ACTUAL_BETTOR_FILL_PROBABILITY`. Protocol prepared in
`MICRO_LIVE_VALIDATION.md`. Requires explicit approval, capital and a
credential. **Not sought here.** `MICRO_LIVE_AUTHORIZED = NO`.

---

## SCHEDULER AND EVIDENCE HYGIENE

```
CRON_CADENCE       "0 */2 * * *" UTC, group beta48-forward-capture,
                   cancel-in-progress: false -- a late segment queues
POLICY             C. ONLY THE FIRST VALID RUN AFTER THE FREEZE ENTERS AS
                   PRIMARY EVIDENCE. Later runs are REPLICATION ONLY and are
                   reported whether they agree or not.
SEG_8              PRIMARY (dispatched 02:40Z, sealed 03:32Z)
SEG_9              TEMPORAL_REPLICATION (scheduled, underpowered)
SEG_10             SALTED-HASH REPLICATION, PRIMARY for the hash-mechanism
                   question only; UFC_ONLY for generalization
```

A two-hourly cron plus a free choice of which segment to call the result is
optional stopping with extra steps. Policy C keeps the free replication and
denies it the power to change a verdict.

```
No orders. No capital. No credentials. No production activation.
No Track A modification. No Phase X re-dispatch. mirror_live = false.
```
