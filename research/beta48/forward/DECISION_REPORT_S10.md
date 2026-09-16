# §10 MAKER-ENGINE DECISION REPORT

Evidence set frozen in `EVIDENCE_SET_FREEZE.md`, committed `c51096e`, BEFORE
either segment's economics were read. Roles were not chosen after seeing which
looked better.

```
MAKER_ENGINE_GATE            = BLOCKED
PRIMARY_SEGMENT              = 35048890640  (dispatched, sealed 03:32Z,
                               16 markets x 40 rounds, 19.5 min per market)
TEMPORAL_REPLICATION_SEGMENT = 35067690562  (SCHEDULED, sealed 07:49Z,
                               8 markets x 3 rounds, 1.0 min per market)
MARKET_OVERLAP_RATE          = 8/8 = 100.0%   (event overlap 6/6 = 100.0%)
```

## THE OVERLAP, MEASURED BEFORE THE ECONOMICS

```
SEGMENT8_UNIQUE_MARKETS  16
SEGMENT9_UNIQUE_MARKETS   8
MARKET_OVERLAP_COUNT      8      segment 9 is a strict SUBSET of segment 8
MARKET_OVERLAP_RATE       100.0%
EVENT_OVERLAP_COUNT       6
EVENT_OVERLAP_RATE        100.0%
UNION_UNIQUE_MARKETS      16     NOT 24
```

The freeze's prediction held exactly. Segment 9 contributes **zero new
markets**. Its 24 panel rows are repeated temporal observations of markets
segment 8 already covered, and they are not concatenated to anything.

## THE RESULT

```
PRIMARY_RESULT               = BLOCKED_ON_UNMEASURED_FILL_TERMS,
                               WITH A MEASURED ADVERSE SIGNAL ON
                               OPPORTUNITY_FREQUENCY
TEMPORAL_REPLICATION_RESULT  = UNDERPOWERED — 16 consecutive pairs over 1.0
                               minute per market; it cannot confirm or refute
                               anything about arrival rates
DIRECTIONALLY_CONSISTENT     = YES   (both show a one-tick spread and a book
                               that does not move)
ECONOMICALLY_CONSISTENT      = NOT_IDENTIFIED (segment 9 observed no trades at
                               all, so there is no economic quantity to compare)
MATERIAL_REGIME_DIFFERENCE   = NOT_IDENTIFIED
```

Segment 9 does not rescue anything and does not contradict anything. It is
reported here at the same prominence it would have had if it had disagreed.

## THE LEDGER

```
TRADING_EDGE_EX_INCENTIVES       = NOT_IDENTIFIED
MAKER_REBATE_CONTRIBUTION        = MEASURED AS A RATE, NOT AS A CONTRIBUTION:
                                   26.5% of a one-tick spread at clips >= ~100
                                   contracts; $0.00 at a 1-contract clip
LIQUIDITY_INCENTIVE_CONTRIBUTION = NOT_IDENTIFIED (share unknowable; see below)
FILL_INCENTIVE_CONTRIBUTION      = NOT_IDENTIFIED
VOLUME_INCENTIVE_CONTRIBUTION    = NOT_IDENTIFIED
TOTAL_EXPECTED_NET               = NOT_IDENTIFIED
ADVERSE_SELECTION                = NOT_IDENTIFIED
RESIDUAL_INVENTORY_COST          = NOT_IDENTIFIED
OPPORTUNITY_FREQUENCY            = MEASURED — see below
CAPITAL_OCCUPANCY                = BOUNDED BELOW — see below
CURRENT_PANEL_SCOPE              = UFC, 16 markets, one fight card
GENERALIZATION_STATUS            = PANEL_GENERALIZES_TO_BOARD = NO,
                                   COVERAGE_BIAS = YES, OUTCOME_LEAKAGE = NO
BIGGEST_REMAINING_UNKNOWN        = ACTUAL_BETTOR_FILL_PROBABILITY
SHORTEST_NEXT_EXPERIMENT         = the salted-hash panel
```

`TOTAL_EXPECTED_NET` is `NOT_IDENTIFIED` and not a number, because four of its
terms are. A sum containing an unmeasured term is not a smaller number; it is
not a number.

## WHAT WAS ACTUALLY MEASURED — AND IT IS NOT ENCOURAGING

From 624 consecutive 30-second snapshot pairs across 16 markets, 19.5 minutes
each:

```
TOUCH_PRICE_MOVED               1/624   = 0.16%
ANY_TRADE_IN_INTERVAL          12/624   = 1.92%   -> 1 trade per 26 minutes
                                                     per market
MEDIAN_TRADE_SIZE              52.32 contracts
SIZE_MOVED_AT_UNCHANGED_PRICE  68/624   = 10.90%
SPREAD_IN_TICKS                median 1   (1 tick 587, 2 ticks 40, 19 ticks 13)
BEST_BID_SIZE_MEDIAN           204.41 contracts
BEST_ASK_SIZE_MEDIAN           68.00 contracts
```

**The queue arithmetic that follows is the finding.** Under the venue's
documented price-time priority, a quote joining at the touch sits behind the
size already there:

```
BID  204.41 ahead / 52.32 per trade = 3.9 median trades
     -> 102 min if EVERY trade hit the bid; ~203 min at 50/50
ASK   68.00 ahead / 52.32 per trade = 1.3 median trades
     ->  34 min if EVERY trade hit the ask;  ~68 min at 50/50
```

So on the bid side a maker waits **on the order of three hours to reach the
front of the queue**, and then needs a further trade to be filled. The reward
for the completed round trip, if both sides ever fill:

```
SPREAD_CAPTURE_1_TICK        $0.0100 per contract
MAKER_REBATE_BOTH_SIDES      $0.0053 per contract (at p = 0.305)
GROSS_IF_BOTH_SIDES_FILL     $0.0153 per contract
AT THE MEDIAN TRADE SIZE     $0.80 per completed round trip
```

Against that: hours of capital occupancy per side, inventory risk on a single
fighter for the whole waiting period, and an adverse-selection cost that is
`NOT_IDENTIFIED` and subtracted from $0.0153, not from a larger number.

`OPPORTUNITY_FREQUENCY` is the one input the maker case needed that this
capture could reach without a trade tape, and it came back low.

## A STRUCTURAL FINDING FOR A SMALL-SCALE BETA

The fee is banker-rounded to the cent **per fill**, so the maker rebate is not
linear in clip size near zero — it is zero:

```
clip     rebate    rebate as % of the 1-tick spread
   1     $0.00       0.0%      <- a one-contract fill earns NOTHING
   2     $0.01      50.0%
   5     $0.01      20.0%
  10     $0.03      30.0%
 100     $0.26      26.0%
1000     $2.65      26.5%
```

And the threshold moves with price, because the fee carries a `p(1-p)` term
that collapses in the tails:

```
p = 0.02  -> 21 contracts before the rebate is non-zero
p = 0.05  ->  9
p = 0.10  ->  5
p = 0.305 ->  2
```

For a programme whose stated goal is "the smallest scientifically defensible
beta", this is directly on point: **the rebate leg of the maker case does not
exist at a one-contract clip, and is weakest exactly at the longshot prices
where a small operator's capital goes furthest.** It is not a rounding detail.

## WHY THE GATE IS BLOCKED AND NOT FAIL

Under the rule frozen in `DECISION_REPORT_TEMPLATE.md`: while fill probability,
queue position and adverse selection are `NOT_IDENTIFIED`, the gate is BLOCKED.
It is not FAIL, because nothing here establishes that a maker engine loses
money — the terms that would decide it were never measured. It is emphatically
not PASS.

Answering the gate from an invented fill probability would be worse than
leaving it unanswered, and the measured opportunity frequency is reported
beside the BLOCKED verdict rather than folded into it.

```
ACTUAL_BETTOR_FILL_EVIDENCE = NOT_IDENTIFIED
```

## WHAT WOULD MOVE IT, IN ORDER OF COST

1. **The salted-hash panel.** Cheapest by far, already built, and it answers a
   different question from anything above: whether a one-tick spread, a static
   touch and a 26-minute trade interval are facts about the BOARD or facts
   about one UFC card. Every number in this report is currently the latter.
2. **The public tape, once the C-14 gate clears.** Gives `TIME_TO_QUEUE
   _DEPLETION` and `MARKOUT_AFTER_COUNTERFACTUAL_MAKER_FILL` — which is the
   adverse-selection term — on symbol-days that reconcile and carry no block
   volume. Blocked until then.
3. **Institutional read-only L2.** Continuous book state instead of 30-second
   polling; converts four blocked terms from NO to PROXY_ONLY and none to YES.
4. **FIX MBO.** The only path that answers the counterfactual outright.
5. **Micro-live passive orders.** The only thing that ever answers
   `ACTUAL_BETTOR_FILL_PROBABILITY`. Requires explicit approval and is not
   sought here.

## SCHEDULER HYGIENE

```
SCHEDULED_RUNS_EXIST      = YES
CRON_CADENCE              = "0 */2 * * *"  (every 2 hours, UTC)
CONCURRENCY_BEHAVIOR      = group beta48-forward-capture,
                            cancel-in-progress: false
                            -> a late segment queues; it is not cancelled
WHICH_RUNS_ENTER_FORMAL_EVIDENCE = policy C (below), applied retroactively
                            here and declared for all future experiments
```

**The ambiguity this removes.** A two-hourly cron plus a free choice of which
segment to call the result is optional stopping with extra steps: keep looking
until a segment agrees with you. Segment 9 was scheduled, not dispatched, but
that is a fact about how it started and not a defence — the defence is that its
role was fixed in writing before it was read.

**Policy for every future formal experiment, stated before launch:**

```
C. ONLY THE FIRST VALID RUN AFTER THE FREEZE ENTERS AS PRIMARY EVIDENCE.
   Later runs are REPLICATION ONLY and are reported whether they agree or not.
```

Not A (all runs enter), which makes the sample grow until it says something.
Not B (first valid run only), which discards free replication. C keeps the
replication and denies it the power to change the verdict.

## STANDING

No orders. No capital. No credentials. No production activation. No Track A
modification. No Phase X re-dispatch. `mirror_live = false`.
