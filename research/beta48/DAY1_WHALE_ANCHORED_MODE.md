# DAY-1 WHALE-ANCHORED MODE

```
WHALE_ANCHORED_MODE_ACTIVE = False        <-- DESIGNED, NOT ACTIVATED
WHALE_ANCHORED_MODE_IS = A_LAUNCH_SAFEGUARD_NOT_A_PERMANENT_RESTRICTION

OFFLINE. VENUE_CONTACT = 0  ORDERS = 0  CAPITAL = 0  mirror_live = false
```

Code: `shadow/whale_bridge.py::day1_admission`.

## The problem it solves

BETTOR is moving from copying whale fills to native trade selection. The
unsupported leap to avoid is:

> "these whales historically earned money" → "our native software EV will be as
> good as theirs on Day 1"

Nothing in the whale archive supports the second claim. `WHALE_ORDER_POLICY` is
`NOT_IDENTIFIED`; we never observed their unfilled quotes, their cancels or the
opportunities they declined. We can replicate a **mechanism**. We cannot claim
to have recovered a **policy**, and we certainly cannot claim to have improved
one we never saw.

## The two routes

```
ROUTE A   WHALE_SUPPORTED_MECHANISM_PLUS_POSITIVE_BETTOR_EV
          whale support (STRONG or MODERATE) for the SAME economic
          mechanism/state
          AND positive current BETTOR EV
          -- the conjunction is required; neither alone admits

ROUTE B   INDEPENDENTLY_VALIDATED_STRUCTURAL_OPPORTUNITY
          an edge that does not depend on an unproven directional model
          -- needs no whale support at all
```

If `WHALE_SUPPORT_LEVEL = OUT_OF_DISTRIBUTION`, a native opportunity remains
**SHADOW** unless separately promoted through Route B.

## What the function returns, and what it never returns

`day1_admission()` returns `ADMIT_CAPITAL_CANDIDATE` or `SHADOW`. It never
returns an instruction to trade, and every result carries `ACTIVATED: False`.
It does not read `WHALE_ANCHORED_MODE_ACTIVE` — **a design that could be
switched on by flipping a constant is not a safeguard.** Activation is a
separate, explicit decision with its own evidence bar.

## Why Route A requires the conjunction

Whale support alone would be copying with extra steps — and copying a mechanism
into a state where our own EV is negative is how a good mechanism becomes a bad
trade. Positive BETTOR EV alone would be trusting an unvalidated native model on
day one, which is precisely the leap management is right to resist.

The conjunction says: *we only put capital where the whales' evidence and our
own model agree*, at launch. Disagreement is not an error — it is the most
informative row in the dataset, and it goes to shadow where it can be measured.

## What Day 1 actually looks like given today's evidence

From `WHALE_REFERENCE_PRIORS_V1.json`, the cell space is
`ACCOUNT × PRICE_BAND` and `ACCOUNT × TIME_UNPAIRED_INTERVAL` — not sport, not
league, not market type. So on Day 1:

- **Route A can only be evaluated on the coarse cells that exist.** The
  four-account merge-sign consensus below 0.50 is the strongest available
  support, for the COMPLETION mechanism, at `VENUE_EQUIVALENCE = PARTIAL`.
- **Support attaches to a SIGN, not a magnitude.** Route A can say "the whales'
  evidence points this way here"; it cannot say how much. The size comes from
  BETTOR's own EV and `EDGE_LOWER_CONFIDENCE_BOUND`.
- **Most native opportunities will be `NOT_IDENTIFIED`, not supported** — there
  is no measurement in their cell. `NOT_IDENTIFIED` routes to SHADOW, which is
  the correct and unexciting answer.

## How BETTOR earns autonomy

This is a launch safeguard, and it is supposed to expire.

```
PRIOR_WEIGHT  =  k / (n + k)       n = BETTOR's own EFFECTIVE observations
NATIVE_WEIGHT =  n / (n + k)       on THAT component
```

Two properties, both pinned by tests:

- **The prior's own size buys it nothing.** A whale n of 10⁹ and of 100 give the
  same native weight. A large archive cannot outvote BETTOR forever.
- **A declared regime shift zeroes the prior rather than decaying it.** If the
  world changed, the old evidence is about a different world.

Route A's requirement relaxes as `NATIVE_WEIGHT` rises on the components that
matter — completion probability, close cost, residual outcome — and the mode
retires when BETTOR's own out-of-sample evidence clears the bar in
`BETTOR_MODEL_VALIDATION_STANDARD_V2.md` on its own.

## What would cause BETTOR to refuse despite whale support

- any feasible action unpriced → `allocate()` declines (already in code)
- `EDGE_LOWER_CONFIDENCE_BOUND` below the price → no trade, no threshold needed
- `VENUE_MECHANISM_EQUIVALENCE = NONE` → support capped at WEAK
- toxicity state high → widen, cancel, wait, or no-trade, **without changing the
  event prediction**
- fee regime straddle unresolved, or `TIER_VERIFIED = False` where the tier
  changes the sign
- event-level exposure already at cap — the allocation unit is the EVENT
