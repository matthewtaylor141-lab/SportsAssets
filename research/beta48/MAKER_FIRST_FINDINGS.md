# BETA48 — maker-first, measured against retained evidence

Run under `BETTOR_ARCHITECTURE.md`. Nothing here reopens Engine A's
first-leg-band entry rule, and nothing here is a new whale study — it is
the same retained CLOB snapshot, read for **execution** economics rather
than prediction.

---

## The finding that reframes the sprint

Engine B's kill rested on an identity:

```
mid(leg0) - ask(leg0) = -spread/2      exactly, every row
```

Taking always pays half the spread. That is an argument **for**
maker-first, not against trading: the spread is the largest measurable
economic quantity in this dataset, and it is an **execution** prize, not
a prediction prize.

| quantity | measured | size |
|---|---|---|
| executable round-trip spread (CLOB) | yes | **1.00 c** median |
| fair-value edge at the mid (M1 train) | yes | **0.23 c**, CI spans 0 |

**The execution channel is roughly 4× the prediction channel.** That is
the case for maker-first stated in numbers.

So the question became: what happens when a BETTOR offer actually gets
filled?

---

## An observed-fill measurement, not a fill assumption

Every one of the 214,609 probe rows carries `side = BUY`. RN1 only
buys in this dataset. That means **a maker resting an offer at his price
was filled — the fill is observed, not modelled.** `TOUCH ≠ FILL` is
respected because no touch is being counted; a trade occurred.

Sample: 112,553 trades across **9,337 independent conditions**, each
with a one-hot settlement whose `resolved_at` is strictly after the
trade (1,684 already-resolved rows dropped).

A maker who sells at `p` and pays out the settlement earns `p − settle`.

```
EXPECTED_MAKER_NET_VALUE (offer side, this flow, before rebates)
    = -0.0090 per share
      95% CI [-0.0143, -0.0038], clustered by condition
```

**Resting an offer that RN1 lifts loses 0.90 cents per share.** The
interval excludes zero.

### The maker ledger, per share, offer side

```
SPREAD_CAPTURE                    +0.0050   (half the measured round trip)
FAIR_VALUE_EDGE                   NOT_IDENTIFIED
ADVERSE_SELECTION                 -0.0140   (residual: net minus spread)
MAKER_REBATES                     NOT_IDENTIFIED (CLOB schedule unverified)
LIQUIDITY_REWARDS                 NOT_IDENTIFIED (eligibility unverified)
--------------------------------------------------------------
EXPECTED_MAKER_NET_VALUE          -0.0090
```

**The spread capture is real and it is not enough.** Adverse selection
is 2.8× the half-spread it earns.

```
BREAK-EVEN MAKER REBATE REQUIRED = 0.90 c/share
                                 = 1.81% of notional on a 50c contract
```

A rebate at that level would be extraordinary. This is exactly the case
the architecture names: *a rebate must never rationalise a fundamentally
negative trade.* The trade is negative by 0.90 c before any rebate is
counted, and no rebate may be counted at all until verified **and** a
fill occurs.

The taker on the other side earns the mirror image, +0.0090/share —
which is simply RN1 being informed. Nothing here says market making is
unprofitable in general; it says **making a market to this particular
informed flow, at the touch, loses money.**

### Post-hoc structure — recorded, NOT promoted

Splitting that P&L by the price he paid:

| band | trades | conditions | P&L/share | 95% CI | ROI on capital |
|---|---|---|---|---|---|
| [0.0,0.1) | 9,661 | 3,264 | −0.0078 | [−0.0150, −0.0006] | −0.83% |
| [0.1,0.3) | 21,167 | 4,263 | **+0.0181** | [+0.0076, +0.0286] | +2.20% |
| [0.3,0.5) | 28,533 | 3,901 | **+0.0276** | [+0.0149, +0.0404] | +4.53% |
| [0.5,0.7) | 27,180 | 3,873 | −0.0493 | [−0.0620, −0.0366] | −12.88% |
| [0.7,0.9) | 18,733 | 4,165 | −0.0411 | [−0.0514, −0.0308] | −21.69% |
| [0.9,1.0) | 7,279 | 2,497 | −0.0147 | [−0.0225, −0.0069] | −25.52% |

Selling into his flow is profitable when he buys cheap-to-mid contracts
and badly unprofitable when he buys favourites.

**This is labelled `POST_HOC_OBSERVATION` and is not promoted, for three
reasons.** It was not preregistered. It is a six-band split on a fresh
measurement — the same shape of search that has already produced one
refuted result this sprint. And the sign pattern is **non-monotone**
(negative, positive, positive, negative, negative, negative), which is
harder to justify economically than a monotone one and therefore more
easily noise or a mixture of effects.

It is also **not** Engine A's rejected rule: that rule entered a naked
first leg on its own open band to harvest future pair completion. This
is a quote/no-quote decision conditioned on the price level of *incoming
flow*, on the offer side, with an observed fill. Different decision,
different channel. Recorded so the distinction is on file — not as a
licence to trade it.

---

## Per-candidate maker field list

Both Engine B candidates are dead (M1 at train, M2 at holdout), so these
are reported for the **generic quote at the touch**, which is what the
evidence can speak to.

```
FAIR_VALUE                      = mid = (ask0 + 1 - ask1)/2   [reconstructible]
PROPOSED_BID                    = NOT_IDENTIFIED — no candidate survived
PROPOSED_ASK                    = NOT_IDENTIFIED — no candidate survived
QUOTE_DISTANCE_FROM_FAIR        = 0.0050 at the touch (half the round trip)
EXPECTED_GROSS_EDGE_IF_FILLED   = +0.0050 spread capture, before selection
MAKER_REBATE_IF_FILLED          = NOT_IDENTIFIED (CLOB schedule unverified)
EXPECTED_SPREAD_CAPTURE         = +0.0050/share
LIQUIDITY_REWARD_EXPECTANCY     = NOT_IDENTIFIED (eligibility unverified)
PASSIVE_FILL_PROBABILITY        = NOT_IDENTIFIED
EXPECTED_TIME_TO_FILL           = NOT_IDENTIFIED
QUEUE_AHEAD                     = NOT_IDENTIFIED
ADVERSE_SELECTION_AFTER_FILL    = -0.0140/share  [MEASURED, RN1 flow]
EXPECTED_MARKOUT_5S             = NOT_IDENTIFIED
EXPECTED_MARKOUT_30S            = NOT_IDENTIFIED
EXPECTED_MARKOUT_60S            = NOT_IDENTIFIED
EXPECTED_MARKOUT_TO_SETTLEMENT  = -0.0090/share  [MEASURED, 9,337 conditions]
INVENTORY_IF_ONLY_ONE_SIDE_FILLS= NOT_IDENTIFIED
PAIR_COMPLETION_PROBABILITY     = NOT_IDENTIFIED for BETTOR
EXPECTED_MAKER_NET_VALUE        = -0.0090/share, before unverified rebates
EXPECTED_TAKER_NET_VALUE        = NOT_IDENTIFIED (no surviving signal to take)
NO_TRADE_VALUE                  = 0
```

`EV_MAKER (-0.0090) < EV_NO_TRADE (0)`, so under the architecture's own
rule the engine **does not quote** here.

### Why the three short-horizon markouts are NOT_IDENTIFIED

A fixed-horizon markout needs a book observation at `t+Δ`. Probes exist
**only when RN1 trades**, so any 5s/30s/60s markout is conditional on
*him trading again inside that window* — precisely the
informed-continuation case. The estimator would be biased toward the
adverse branch by construction, and a number produced that way would
overstate adverse selection while looking precise. Gap distribution, for
the record: 35.6% ≤5s, 16.7% 5–30s, 7.4% 30–60s, 18.1% 60–300s, 22.2%
>300s, median 23.9 s. The data density is there; the **conditioning** is
what disqualifies it, not the sample size.

Settlement markout has no such problem: it is a fixed terminal event
that does not depend on his subsequent behaviour.

---

## Hybrid architecture fields (first leg standing on its own, then pairing)

The hybrid question is: can a BETTOR first leg with positive independent
EV later convert into a pair? It requires a first leg that stands alone.

```
STANDALONE_DIRECTIONAL_EXPECTANCY  = NOT_IDENTIFIED — no Engine B candidate
                                     survived. M1 died at train (bias
                                     +0.0023 vs a 0.0100 half-spread);
                                     M2 died at holdout (+1.47pp vs a
                                     +3.30pp gate, CI lower bound -2.88pp).
PROBABILITY_OF_LATER_PAIR_OPPORTUNITY = NOT_IDENTIFIED for BETTOR.
                                     Reference-account completion rates are
                                     MEASURED but are not BETTOR's, and may
                                     not be substituted.
PAIR_CONVERSION_EXPECTANCY         = NOT_IDENTIFIED
EXPECTED_VALUE_IF_NEVER_PAIRED     = NOT_IDENTIFIED for BETTOR. For the six
                                     reference accounts this is exactly what
                                     killed the band rule: total economics
                                     including unpaired residual.
EXPECTED_EXIT_COST                 = NOT_IDENTIFIED, and now partially
                                     bounded in the ADVERSE direction: the
                                     only observed counterparty that lifts
                                     offers is informed, and selling to it
                                     costs 0.90 c/share versus settlement.
EXPECTED_HOLD_VALUE                = NOT_IDENTIFIED
EXPECTED_HYBRID_NET_VALUE          = NOT_IDENTIFIED
```

The hybrid cannot be evaluated because its **precondition is missing**:
there is no first leg with demonstrated standalone positive expected
value. The architecture is right and the input does not exist yet.

One thing the maker measurement *does* contribute to Engine A's open
unknown: `UNPAIRED_LEG_EXIT_COST` was `NOT_IDENTIFIED`. It still is —
but we now know the one observable class of counterparty that would lift
a resting offer is informed, and filling against it costs 0.90 c/share
against settlement. Exiting an orphan leg into that flow is **not**
free, which is the direction the Engine A gate assumed but could not
show.

---

## What this adds to the standing record

```
EXECUTION_CHANNEL_EXCEEDS_PREDICTION_CHANNEL = YES (1.00c vs 0.23c)
MAKER_AT_TOUCH_VS_RN1_FLOW                   = NEGATIVE, -0.0090/share
ADVERSE_SELECTION_FROM_RN1_BUY_FLOW          = -0.0140/share [MEASURED]
BREAK_EVEN_MAKER_REBATE_VS_THIS_FLOW         = 0.90 c/share
PASSIVE_FILL_PROBABILITY_FOR_BETTOR          = NOT_IDENTIFIED
MAKER_OFFER_PNL_BY_FLOW_PRICE_BAND           = POST_HOC_OBSERVATION
```

Scope, stated rather than assumed: this is Polymarket CLOB, not PMUS;
one counterparty, not the board; 2026-08-06 .. 09-11, not the current
regime; and it measures **being filled by RN1**, not BETTOR's
unconditional fill probability. BLOCK_4 stands — 22,297 displayed shares
against 180 traded in 16 minutes, zero touches.

`mirror_live = false`. Read only. No orders, no capital, no production
activation, no Track A change, no Phase X re-dispatch.
