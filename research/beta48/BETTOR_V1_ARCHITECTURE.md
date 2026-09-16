# BETTOR V1 — FROZEN STRATEGY ARCHITECTURE

Production-neutral. This specifies what the system decides and in what order.
It specifies no credential, no venue connection and no order. It supersedes
nothing in `BETTOR_ARCHITECTURE.md` — it extends it with the execution-choice
and incentive layers the whale cohort and the forward capture made necessary.

```
BETTOR_V1_ARCHITECTURE = FROZEN 2026-09-16
MICRO_LIVE_AUTHORIZED  = NO
mirror_live            = false
```

---

## THE PIPELINE

```
                    INDEPENDENT FAIR VALUE
                             |
                             v
                     MARKET ELIGIBILITY
                             |
                             v
                       OPPORTUNITY
                             |
                             v
        EV_MAKER  /  EV_TAKER  /  EV_NO_TRADE
                             |
                             v
                    EXECUTION CHOICE
                             |
                             v
                       INVENTORY
                             |
                             v
        PAIR  /  HEDGE  /  HOLD  /  EXIT
                             |
                             v
                    CAPITAL RECYCLE
```

Every arrow is a gate that can return NO TRADE. The pipeline is not a funnel
that must produce an order.

---

## 1. INDEPENDENT FAIR VALUE

```
INPUT    whatever signal is available and verified
OUTPUT   FV, an estimate of settlement probability, WITH a stated basis
GATE     FV_BASIS in {MODEL, EXTERNAL_REFERENCE, VENUE_IMPLIED, NOT_IDENTIFIED}
```

**The hard rule.** `FV_BASIS = VENUE_IMPLIED` — reading fair value off the
venue's own mid — is **not an independent fair value**. It is the market's
opinion restated, and a strategy that trades on it is trading on nothing. It
may be recorded; it may not justify an entry.

```
FAIR_VALUE_EDGE_TODAY = NOT_IDENTIFIED
```

This is stated plainly because it is the single largest gap in V1: the cohort
established what does NOT work and the forward capture measured the execution
environment, but no independent fair-value signal has been validated. A V1 beta
that computes `EV_MAKER` from a `NOT_IDENTIFIED` fair value returns NO TRADE,
and that is the correct behaviour, not a bug.

## 2. MARKET ELIGIBILITY

The frozen screen, preregistered before any economics were evaluated against it
(`research/beta48/forward/eligibility.py`):

```
BROAD          status OPEN and two-sided BBO and SPREAD_TICKS <= 5
ACTIVE         BROAD and TRADE_RECENCY <= 24 h
HIGH_ACTIVITY  BROAD and TRADE_RECENCY <= 60 min
```

Nested by construction and asserted per market, not promised. Two standing
constraints ride with it:

- `TRADE_RECENCY` establishes **recency, never a rate**. One timestamp cannot
  produce an arrival rate, and the module computes no interarrival time, no
  trades-per-hour, no Poisson rate and no queue-clearing time. A test bans
  those names from the file.
- Eligibility is recomputed **at stage-2 decision time**, never carried forward
  from a stage-1 routing read up to 1.28 hours stale.

## 3. OPPORTUNITY

```
OPPORTUNITY_COUNT      how many eligible markets exist
INDEPENDENT_CAPACITY   how many INDEPENDENT bets they represent
```

**These are two numbers and V1 never conflates them.** Thirty eligible props on
one NFL game are not thirty independent capital opportunities. The venue
publishes no event identifier (probed 0/20,000 across nine candidate fields), so
today:

```
INDEPENDENT_CAPACITY             = NOT_IDENTIFIED
INDEPENDENT_CAPACITY_LOWER_BOUND = the market-family count
INDEPENDENT_CAPACITY_UPPER_BOUND = the market count  (a CEILING, not an estimate)
```

Event-level exposure limits bind on the **lower** bound. Using the upper bound
for sizing would be assuming independence the evidence does not support.

## 4. EV_MAKER / EV_TAKER / EV_NO_TRADE

Three quantities, computed for every opportunity, never collapsed into one.

```
EV_MAKER  = P(fill) x (FV_EDGE + SPREAD_CAPTURE + MAKER_REBATE)
            - ADVERSE_SELECTION - CAPITAL_OCCUPANCY_COST
            - RESIDUAL_INVENTORY_COST

EV_TAKER  = FV_EDGE - HALF_SPREAD - TAKER_FEE
            - RESIDUAL_INVENTORY_COST

EV_NO_TRADE = 0, and it is a REAL ALTERNATIVE, not a default that loses ties.
```

**Four rules that bind the arithmetic:**

1. **A term that is `NOT_IDENTIFIED` does not evaluate to zero.** An EV
   containing an unmeasured term is `NOT_IDENTIFIED`, not a smaller number.
   A sum with an unknown in it is not a number.
2. **`MAKER_REBATE` is counted only when verified AND a fill occurs.** A rebate
   never rationalises a fundamentally negative trade. The measured case is
   explicit: making a market to informed flow at the touch loses 0.90 c/share,
   and the break-even rebate would be 1.81% of notional on a 50c contract —
   a schedule that does not exist.
3. **`P(fill)` is `ACTUAL_BETTOR_FILL_PROBABILITY` and it is not observable
   without a BETTOR order.** Counterfactual fill models F0–F3 bound it; they do
   not measure it. `TOUCH != FILL`.
4. **`ADVERSE_SELECTION` is subtracted from the small number, not the big one.**
   It is subtracted from spread capture plus rebate, which on the measured board
   is $0.0153/contract — not from some notional gross.

## 5. EXECUTION CHOICE

```
argmax over {MAKER, TAKER, NO_TRADE}, with these overrides:

  any EV is NOT_IDENTIFIED              -> NO_TRADE
  MAKER chosen                          -> POST_ONLY required, always
  clip < MINIMUM_CLIP_FOR_NONZERO_REBATE-> the rebate term is ZERO, not small
  edge is informational and perishable  -> TAKER is permitted (RN1's mechanism)
  edge is structural and patient        -> MAKER is permitted
```

**The rebate-rounding rule is structural, not a detail.** The fee is
banker-rounded to the cent **per fill**, so the maker rebate is not linear in
clip size near zero — it is *zero*:

```
clip  rebate  as % of a 1-tick spread      minimum clip for a non-zero rebate
   1  $0.00      0.0%   <- earns NOTHING     p=0.01  41 contracts
   2  $0.01     50.0%                        p=0.05   9
   5  $0.01     20.0%                        p=0.10   5
  10  $0.03     30.0%                        p=0.30-0.70  2
 100  $0.26     26.0%                        p=0.99  41
1000  $2.65     26.5%
```

For a programme whose goal is the *smallest* defensible beta this is directly
on point: **the rebate leg does not exist at a one-contract clip, and is
weakest exactly at the longshot prices where small capital goes furthest.**

## 6. INVENTORY

Per **leg**, never netted to a market-level position. The whale cohort's
single most expensive lesson is that a profitable pair channel and a
catastrophic residual channel net to a number that describes neither.

```
per leg:  market, side, qty, entry price, entry time, FV at entry,
          EV basis at entry, current FV, age, capital tied
```

## 7. PAIR / HEDGE / HOLD / EXIT

Four actions, evaluated continuously on every open leg.

**The binding rules, frozen:**

```
1.  NO FIRST LEG SOLELY BECAUSE A COMPLEMENT MAY APPEAR LATER.
    This is the rule the cohort killed. kch123's cheapest band merged
    +59.49% and totalled -92.84%: $4,336 merged against -$142,150 settled.

2.  A first leg needs INDEPENDENT ECONOMIC JUSTIFICATION at entry, OR
    immediately attractive pair economics on a simultaneously executable pair.
    "The band predicts future completion" is neither.

3.  A COMPLETED PROFITABLE ECONOMIC PAIR DEFAULTS TOWARD REALIZATION AND
    RECYCLING when venue mechanics make that economically superior.
    Capital that is locked until settlement is capital not working.

4.  COMPLETED PAIRS ARE NOT LEFT IDLE merely to wait for settlement if the
    same economics can be realized or recycled sooner.

5.  UNMATCHED LEGS ARE MANAGED INDEPENDENTLY, on fair value, inventory, time
    and exit economics -- NOT on the hope of a complement.

6.  NEVER FORCE AN UNECONOMIC PAIR COMPLETION. Buying the complement at a bad
    price to make a position "look" flat is a loss taken to tidy a screen.

7.  NEVER FORCE A BID-SIDE CASH-OUT merely to make inventory disappear.
    Symmetric to rule 6 and just as expensive.

8.  SETTLEMENT IS A VALID RESIDUAL-INVENTORY OUTCOME. It is the right answer
    when exit economics are worse. It is the WRONG answer as a default, which
    is what the cohort did: 0.00%-0.54% sell rate, homerunhazard exactly zero.

9.  NO TRADE IS A FIRST-CLASS ACTION at every one of these four decisions.
```

**The exit price is the open unknown.** `UNPAIRED_LEG_EXIT_COST =
NOT_IDENTIFIED` and no reference account supplies it, because none of them
sell. It may not be assumed cheap: a leg goes unpaired precisely because no
counterparty wants that side, which is the same condition that makes it
expensive to exit.

## 8. CAPITAL RECYCLE

```
NORTH_STAR = EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR
```

Not ROI per trade. Not gross edge. A strategy earning $0.0153/contract while
occupying capital for three hours per side is a different business from one
earning the same in three minutes, and only the second scales at small size.

```
EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR = NOT_IDENTIFIED
```

It requires `P(fill)` and `TIME_TO_FILL`, neither of which exists without a
BETTOR order. It must **never** be computed from trade RECENCY as though
recency were a fill rate.

---

## PHASE D — THE SEPARATE P&L LEDGERS, FROZEN

Fifteen ledgers, then three rollups. **Never blended into a headline ROI.**

```
TRADING
  DIRECTIONAL_TRADING_PNL      an entry taken on its own economic merit
  PAIR_COMPLETION_PNL          the merge channel
  RESIDUAL_INVENTORY_PNL       legs that never paired
  EXIT_HEDGE_PNL               legs closed by sale or hedge
  SETTLEMENT_PNL               legs carried to settlement
  SPREAD_CAPTURE               passive half-spread earned
  ADVERSE_SELECTION            what the flow took back
  OTHER_COSTS

VENUE ECONOMICS
  MAKER_REBATES
  TAKER_FEES
  TAKER_REBATES

INCENTIVES
  LIQUIDITY_INCENTIVES
  FILL_INCENTIVES
  VOLUME_INCENTIVES
  NEGOTIATED_MM_INCENTIVES

ROLLUPS
  TRADING_NET_EX_INCENTIVES  = the eight TRADING lines + the three VENUE lines
  INCENTIVE_CONTRIBUTION     = the four INCENTIVE lines
  TOTAL_NET                  = TRADING_NET_EX_INCENTIVES + INCENTIVE_CONTRIBUTION
```

### The two rules these ledgers exist to enforce

```
1.  NEVER ALLOW PROFITABLE PAIR P&L TO HIDE LOSING RESIDUAL INVENTORY.
    PAIR_COMPLETION_PNL and RESIDUAL_INVENTORY_PNL are reported side by side
    at equal prominence, always. This is kch123's lesson and it is not
    negotiable: +59.49% and -92.84% are the same band.

2.  NEVER ALLOW INCENTIVES TO HIDE NEGATIVE TRADING ECONOMICS.
    TRADING_NET_EX_INCENTIVES is reported BEFORE and INDEPENDENTLY of
    TOTAL_NET. If TRADING_NET_EX_INCENTIVES < 0, the strategy is
    INCENTIVE_DEPENDENT and is labelled so in every report, whatever
    TOTAL_NET says.
```

### Two maker business models, kept apart

```
MODEL A  STRUCTURAL       TRADING_NET_EX_INCENTIVES > 0.
                          The incentive is a bonus.
MODEL B  INCENTIVE_SUPPORTED
                          TRADING_NET_EX_INCENTIVES <= 0, TOTAL_NET > 0.
                          The business IS the incentive, and it lives or dies
                          by a programme the venue can change or end.
```

Both are legitimate businesses. They are **not the same business**, they carry
different risks, and a report that does not say which one it is describing is
not a report. V1 labels every result with its model.

### Not board-wide profitability

A maker engine does not need every eligible market to be profitable. It needs a
**selected subset** to be, plus the discipline to quote only there. Requiring
board-wide profitability would kill a viable strategy for the wrong reason.
Correspondingly, `MAKER_ELIGIBLE_UNIVERSE_V1` is a preregistered
market-selection experiment with thresholds frozen before economics — not a
threshold search over outcomes.

---

## KILL SWITCHES, SPECIFIED HERE AND INERT

```
STALE_DATA_KILL       quote age exceeds the frozen bound -> cancel all, stop
QUOTE_AGE_KILL        a resting order older than the bound -> cancel
INVENTORY_KILL        per-leg or per-event exposure breach -> stop opening
LOSS_KILL             realized loss breach -> stop opening, manage exits only
EVENT_EXPOSURE_KILL   bound computed on INDEPENDENT_CAPACITY_LOWER_BOUND
MASS_QUOTE_PROTECTION where the venue offers it -- a BACKSTOP, never a fill cap
```

`MQP_IS_HARD_MAX_FILL_LIMIT = False`. BETTOR's own position and event limits do
the work; the venue's protection is the last line, not the first.

---

## WHAT V1 DELIBERATELY DOES NOT DO

```
- It does not connect to a venue.
- It does not hold a credential.
- It does not place an order.
- It does not assume a fill from a touch.
- It does not count an unverified rebate.
- It does not treat NOT_IDENTIFIED as zero.
- It does not net a pair channel against a residual channel.
- It does not report TOTAL_NET without TRADING_NET_EX_INCENTIVES beside it.
- It does not size on an independence assumption the evidence rejects.
```

```
mirror_live = false. No credentials. No authenticated connection. No orders.
No capital. No production activation. No Track A modification.
No Phase X re-dispatch.
```
