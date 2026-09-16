# BETTOR_V1_BETA_SPEC — the smallest defensible beta

```
SHADOW_ONLY            = YES
READ_ONLY              = YES
ORDER_CAPABLE          = NO
CREDENTIAL_REQUIRED    = NONE
MICRO_LIVE_AUTHORIZED  = NO
mirror_live            = false
```

**No order-capable credential is required to build or run this beta.** That is
a design property, not a limitation: every capability below is exercisable
against public, unauthenticated reads, and the one thing it cannot do —
establish `ACTUAL_BETTOR_FILL_PROBABILITY` — is the one thing no amount of
building can substitute for.

The beta's job is to be **completely built and completely exercised** so that
when execution validation is eventually authorized, the only new thing in the
system is the order itself.

---

## THE SIXTEEN CAPABILITIES

### 1. DISCOVER CANDIDATE MARKETS

```
INPUT    public board enumeration
OUTPUT   the observed prefix, with its boundary stated
FIELDS   OBSERVED_PREFIX_MARKETS, DISCOVERY_LIST_EXHAUSTED,
         PAGINATION_ADVANCED, TRUE_ACTIVE_BOARD_SIZE
```

`DISCOVERY_LIST_EXHAUSTED = NO` and `TRUE_ACTIVE_BOARD_SIZE = NOT_IDENTIFIED`
are reported on every run. The beta never calls an observed prefix "the board".

### 2. APPLY THE FROZEN ELIGIBILITY / ROUTING RULES

```
STAGE 1 (free, from the board row)   -> BROAD
STAGE 2 (one book read per market)   -> ACTIVE, HIGH_ACTIVITY
```

Nested tiers, thresholds frozen before economics. Reason codes are ordered by
`REASON_PRIORITY` so two runs over the same row always agree on the primary
reason. **Eligibility is recomputed at stage-2 decision time**, never carried
forward from a stage-1 read up to 1.28 hours stale, and every row carries its
own observation clock.

Routing censoring is audited, not assumed: an audit lane of stage-1 rejects is
interleaved by salted hash, and `audit_elapsed_report()` reports
`ROUTED_ELAPSED_P10/P50/P90` against `AUDIT_ELAPSED_P10/P50/P90` so a fair
schedule *position* is never mistaken for a fair observation *time*.

### 3. CALCULATE THE AVAILABLE FAIR-VALUE SIGNAL

```
OUTPUT   FV, FV_BASIS, FV_CONFIDENCE
```

`FV_BASIS = VENUE_IMPLIED` is recorded and **may not justify an entry** — it is
the market's opinion restated. Today `FAIR_VALUE_EDGE = NOT_IDENTIFIED`, so the
beta returns NO TRADE on every market. **That is the specified behaviour.** A
beta that produced trades from an unidentified fair value would be worse than
useless; it would be wrong in a way that looks like working.

### 4–6. CALCULATE EV_MAKER, EV_TAKER, EV_NO_TRADE

Three quantities, never collapsed.

```
EV_MAKER = P(fill) x (FV_EDGE + SPREAD_CAPTURE + MAKER_REBATE)
           - ADVERSE_SELECTION - CAPITAL_OCCUPANCY_COST
           - RESIDUAL_INVENTORY_COST
EV_TAKER = FV_EDGE - HALF_SPREAD - TAKER_FEE - RESIDUAL_INVENTORY_COST
EV_NO_TRADE = 0
```

**Propagation rule, enforced in code and tested:** any `NOT_IDENTIFIED` input
makes the EV `NOT_IDENTIFIED`. It does not default to zero, and it does not
silently drop out of the sum. The beta must be able to say "I do not know"
about a specific market and keep running.

### 7. SELECT THE EXECUTION MODE

```
argmax {MAKER, TAKER, NO_TRADE}
  any EV NOT_IDENTIFIED -> NO_TRADE
  MAKER                 -> POST_ONLY, always, no exceptions
  tie                   -> NO_TRADE wins ties
```

### 8. APPLY QUOTE-SIZE AND REBATE-ROUNDING RULES

```
Fee = THETA x C x p x (1-p), banker's rounded to the cent, PER FILL
THETA_TAKER  0.06 (JUL2026) -> 0.0695 (SEP2026)
THETA_MAKER -0.0125
CUTOVER      2026-09-16 23:59 ET = 2026-09-17 03:59 UTC
             (the ET and UTC dates differ -- this is the C-6 trap)
```

Before quoting, the beta computes `MINIMUM_CLIP_FOR_NONZERO_REBATE(p)` and, if
the intended clip is below it, sets `MAKER_REBATE = 0` exactly — not a small
number. The floor is U-shaped in price (41 contracts at p=0.01, 2 at p=0.30–0.70,
41 at p=0.99).

### 9. MAINTAIN PER-LEG INVENTORY

Per **leg**, never netted to a market position. Netting is how a profitable
pair channel and a catastrophic residual channel become one meaningless number.

### 10. RECOGNIZE COMPLETED ECONOMIC PAIRS

A pair is complete when complementary legs are held such that the combined
position has locked economics — recognized from the **positions**, not from an
intention to pair later.

### 11. CHOOSE PAIR / HEDGE / HOLD / EXIT

All nine binding rules from `BETTOR_V1_ARCHITECTURE.md` §7 are implemented and
tested, including the two that prevent cosmetic trading:

```
NEVER force an uneconomic pair completion.
NEVER force a bid-side cash-out merely to make inventory disappear.
SETTLEMENT is a valid residual outcome -- and never a default.
```

### 12. MAINTAIN EVENT-LEVEL EXPOSURE LIMITS

Bound on `INDEPENDENT_CAPACITY_LOWER_BOUND` — the market-family count — **not**
on the market count. Using the market count would assume an independence the
evidence rejects: 4,783 markets are proved to be only 72 contests on the
stratum where the venue's own fields settle it.

Since `EVENT_KEY_VALIDATED = NO`, the beta treats every market in a family as
fully correlated. That is conservative and it is deliberate.

### 13. MAINTAIN CAPITAL-ALLOCATION LIMITS

Tracks capital **occupied over time**, not just deployed. The north star is
`EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR`, currently `NOT_IDENTIFIED`, and
it must **never** be computed from trade recency as though recency were a fill
rate.

### 14. CALCULATE ALL P&L LEDGERS SEPARATELY

All fifteen ledgers and three rollups from `BETTOR_V1_ARCHITECTURE.md` Phase D.
`TRADING_NET_EX_INCENTIVES` is reported **before and independently of**
`TOTAL_NET`; if it is negative the run is labelled `INCENTIVE_DEPENDENT` in
every output regardless of `TOTAL_NET`.

In shadow these are counterfactual and **every one of them is labelled
`COUNTERFACTUAL`**. A shadow P&L is never reported as a realized P&L.

### 15. INVOKE KILL SWITCHES

```
STALE_DATA_KILL   QUOTE_AGE_KILL   INVENTORY_KILL
LOSS_KILL         EVENT_EXPOSURE_KILL
MASS_QUOTE_PROTECTION (a BACKSTOP, never a fill cap)
```

Every switch is exercised in shadow. A kill switch first tested in production
is not a kill switch.

### 16. REMAIN SHADOW / READ-ONLY UNTIL EXPLICIT AUTHORIZATION

```
ORDER_PATH_EXISTS   = NO
CREDENTIAL_PATH     = NONE
```

Not a runtime flag that could be flipped by configuration — the order path is
**absent from the build**. The beta is structurally incapable of sending an
order, and a test proves it by walking the AST for network and order calls
rather than grepping for strings. (Substring scans have tripped on prose
stating the guarantee three separate times in this programme; the structural
test is the fix.)

---

## WHAT THIS BETA WILL ACTUALLY OUTPUT ON DAY ONE

Stated in advance so nobody is surprised, and so the result cannot be quietly
reinterpreted later:

```
MARKETS_DISCOVERED               ~20,000 (observed prefix)
STAGE1_BROAD_ROUTED_MARKETS      ~9,182   (not "eligible")
STAGE2_ACTIVE / HIGH_ACTIVITY    measured once the census runs
FV_BASIS                         NOT_IDENTIFIED on essentially all markets
EV_MAKER / EV_TAKER              NOT_IDENTIFIED on essentially all markets
EXECUTION_DECISION               NO_TRADE on essentially all markets
TRADES_SIMULATED                 ~0
```

**A beta that trades nothing is the correct output of a system whose fair-value
signal is unidentified.** Its value is that it proves the pipeline, the
ledgers, the limits and the kill switches all work — so that the day a
fair-value signal *is* validated, the only untested component is that signal.

The alternative — building something that produces trades by relaxing the
`NOT_IDENTIFIED` rule — would produce a beta that looks alive and is measuring
nothing.

---

## WHAT IS EXPLICITLY OUT OF SCOPE FOR V1

```
- Any authenticated connection.
- Any order, of any size, under any condition.
- Any fair-value model not yet validated.
- Any promotion of a counterfactual fill to an actual fill.
- Any incentive counted before it is verified AND a fill occurs.
- Any claim that BETTOR is profitable.
```
