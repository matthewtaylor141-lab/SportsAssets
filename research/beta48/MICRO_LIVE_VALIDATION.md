# MICRO_LIVE_VALIDATION — protocol preparation only

```
MICRO_LIVE_AUTHORIZED = NO
```

**This document activates nothing.** It is written now, before any approval is
sought, so that the protocol is fixed in advance rather than designed in the
moment someone wants to start. Every limit below is frozen here; if a limit is
later relaxed, that relaxation is visible as an edit to this file.

Nothing in this document is a request for authorization. It is a specification
of what authorization would mean, so that the decision, when it is made, is
made against a fixed target.

---

## WHY THIS EXPERIMENT AND NO OTHER

Exactly one quantity requires it:

```
ACTUAL_BETTOR_PASSIVE_FILL_PROBABILITY
```

Every cheaper path — REST snapshots, aggregated L2, the public tape,
institutional read-only L2, even FIX MBO — yields at best
`HIGH_FIDELITY_COUNTERFACTUAL_FILL_SIMULATION`. A counterfactual fill is a
statement about a book that did not contain our order. **`TOUCH != FILL`**, and
no fidelity of observation converts one into the other.

The corollary is the reason this document exists at all: **if micro-live is
never authorized, that unknown stays unknown forever**, and
`MAKER_ENGINE_GATE_V2` stays BLOCKED permanently. That is an acceptable outcome
and it should be an explicit choice, not a drift.

---

## HARD LIMITS — FROZEN

```
MINIMUM_CLIP_FOR_NONZERO_REBATE_BY_PRICE
    p=0.01  41 contracts      p=0.30   2 contracts
    p=0.02  21                p=0.50   2
    p=0.05   9                p=0.70   2
    p=0.10   5                p=0.90   5
    p=0.20   3                p=0.95   9
                              p=0.99  41

    The clip is sized to the price band. Quoting below the floor earns a
    rebate of EXACTLY ZERO, which makes the experiment measure a maker
    strategy nobody would run. Prices in 0.30-0.70 are preferred for the
    first probes: the floor there is 2 contracts, the cheapest place on the
    board to buy a real measurement.

MAX_DOLLAR_RISK_PER_ORDER        $25
MAX_DOLLAR_RISK_PER_EVENT        $50     (on the FAMILY key, not the market)
MAX_TOTAL_LIVE_EXPOSURE          $250
MAX_CONCURRENT_RESTING_ORDERS    10
MAX_DAILY_NEW_EXPOSURE           $250

POST_ONLY_REQUIRED               YES -- every order, no exceptions.
                                 A post-only rejection is a DATA POINT to be
                                 recorded, never a prompt to re-send crossing.
SELF_MATCH_PREVENTION_REQUIRED   YES. Two BETTOR orders on opposite sides of
                                 one market must not trade with each other;
                                 a self-match would produce a fabricated fill
                                 and corrupt the single measurement being made.
```

The exposure numbers are deliberately small enough that the entire experiment
can lose its whole budget without the result mattering financially. **The
budget is the price of the measurement, not a position.**

## KILL SWITCHES — ARMED BEFORE THE FIRST ORDER

```
STALE_DATA_KILL       book data older than 5 s -> cancel all, stop
QUOTE_AGE_KILL        a resting order older than 30 min -> cancel it
                      (its time-to-fill is recorded as CENSORED, not as a
                      failure to fill -- a censored observation is data)
INVENTORY_KILL        any per-order / per-event / total limit breached
                      -> cancel all, stop opening, manage exits only
LOSS_KILL             $50 realized loss -> full stop, no re-arm without an
                      explicit new authorization
MASS_QUOTE_PROTECTION where the venue offers it. A BACKSTOP.
                      MQP_IS_HARD_MAX_FILL_LIMIT = False -- BETTOR's own
                      limits do the work; the venue's is the last line.
```

Every switch is exercised in shadow **before** the first live order. A kill
switch first tested in production is not a kill switch.

## PRIMARY MEASUREMENTS

These are the deliverables. Everything else is instrumentation.

```
ACTUAL_ORDER_ACCEPTANCE_LATENCY   submit -> venue ack, per order
ACTUAL_QUEUE_POSITION             displayed size ahead at the moment of ack
ACTUAL_PASSIVE_FILL_RATE          filled orders / accepted resting orders
ACTUAL_ADVERSE_SELECTION          markout at +1m, +5m, +30m and at settlement,
                                  sign-corrected per side
ACTUAL_TIME_TO_FILL               ack -> first fill, with censored
                                  observations retained and labelled
ACTUAL_CAPITAL_OCCUPANCY          dollar-hours per order and per fill
ACTUAL_NET_PER_CAPITAL_HOUR       the north star, measured rather than modelled
```

### Measurement discipline, fixed in advance

```
1.  A CANCELLED-UNFILLED ORDER IS AN OBSERVATION, not a discarded trial.
    Dropping unfilled orders from the denominator would inflate the fill rate
    by exactly the quantity being measured. The denominator is ACCEPTED
    RESTING ORDERS, always.

2.  ADVERSE SELECTION IS SIGN-CORRECTED PER SIDE. A maker who buys and a maker
    who sells have opposite exposure to the same price move; averaging them
    unsigned would report zero for a strategy losing on both.

3.  THE REBATE IS COUNTED ONLY WHEN A FILL OCCURS AND THE CREDIT IS OBSERVED
    IN THE LEDGER. Not when the venue's schedule says it should be.

4.  EVERY LEDGER FROM PHASE D IS KEPT SEPARATE, including at this size.
    TRADING_NET_EX_INCENTIVES is reported before TOTAL_NET.

5.  INFERENCE CLUSTERS AT THE FAMILY LEVEL, not the market level. Ten resting
    orders across ten props on one fight is closer to one observation than to
    ten, and EVENT_KEY_VALIDATED = NO means the conservative reading is the
    required one.

6.  THE SAMPLE SIZE IS DECLARED BEFORE THE FIRST ORDER AND NOT EXTENDED
    BECAUSE THE RESULT IS UNWELCOME. A pre-declared stopping rule is the
    difference between a measurement and a search.
```

## SIZING THE EXPERIMENT AGAINST THE MEASURED ENVIRONMENT

The forward capture makes the cost estimable in advance, which is the point of
having done it first:

```
MEASURED: one trade per 26.0-44.9 market-minutes on the UFC incentive panel
MEASURED: queue ahead 1.26-1.32 median trades -> 1-2 h to the front
MEASURED: 16 of 23 panel markets traded ZERO times in 19.5 minutes each

IMPLICATION: a resting order in that environment may wait HOURS for a fill,
and most will be cancelled unfilled at the 30-minute QUOTE_AGE_KILL.
```

So a probe confined to the UFC incentive panel would spend its budget
collecting mostly **censored** observations. Two consequences, both decided
here rather than later:

1. **The stage-2 rolling census runs first.** It is free, needs no new
   authority, and identifies where on the remaining 98.1% of the board the
   activity actually is. Running a paid experiment into the quietest measured
   corner of the board would be buying the least informative sample available.
2. **The probe targets the measured-active tier**, and its market selection is
   frozen by salted hash **before** the first order, with the salt committed in
   advance.

```
ESTIMATED_ORDERS_FOR_A_USABLE_FILL_SAMPLE   NOT_IDENTIFIED until the census
                                            runs -- and that is precisely why
                                            the census runs first
```

## WHAT AUTHORIZATION WOULD HAVE TO COVER, EXPLICITLY

```
[ ] A credential with order scope          -- does not exist today
[ ] Capital at risk                        -- up to $250 total
[ ] Live orders on a public venue          -- post-only, minimum clip
[ ] A named human accountable for the stop
[ ] A pre-declared stopping rule and sample size
[ ] Agreement that a NEGATIVE result ends the maker engine
```

That last item is the one that matters. An experiment that cannot return a
result which kills the idea is not an experiment. **If micro-live measures a
fill rate and an adverse selection that make `TRADING_NET_EX_INCENTIVES`
negative and `TOTAL_NET` non-positive, the maker engine closes** — and this
document is where that was agreed, before the money was spent.

---

```
MICRO_LIVE_AUTHORIZED = NO
This is protocol preparation only. No credentials. No authenticated connection.
No orders. No capital. No production activation. mirror_live = false.
```
