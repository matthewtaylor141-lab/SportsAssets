# BETTOR TARGET ARCHITECTURE — permanent requirements

Two owner clarifications, recorded as standing architecture rather than
sprint notes. They bind all future work.

---

## 1. PAIRING IS NOT REMOVED. THE STANDALONE ENTRY RULE IS.

What is dead for this sprint is exactly one hypothesis:

> "Enter a first leg primarily because its opening-price band predicts
> profitable future pair completion."

The evidence killed that and nothing wider. Completed-pair MERGE
economics can be very attractive while residual/unpaired inventory
overwhelms them — which invalidates the **entry rule**, not the
**mechanism**.

```
PAIR_TRADING_DOES_NOT_WORK              = NOT CONCLUDED
PAIR_PROFITABILITY_ALONE_JUSTIFIES
  TAKING_NAKED_FIRST_LEG_INVENTORY      = NO   <-- this is what died
PAIRING_REMAINS_A_CORE_CAPABILITY       = YES
```

Preserved empirical result: **RN1 and ferrarichampions2026 showed
substantial positive reconstructed MERGE economics** (+$10.36M and
+$10.80M realized_merge_pnl, reconciled to $0.00). That is real.

### The binding rules

1. BETTOR does **not** acquire a naked first leg merely because
   historical whales profitably paired similar first legs.
2. A first leg needs **independent economic justification at entry**,
   unless a simultaneously executable structural pair already provides
   sufficient locked economics.
3. Once BETTOR owns a justified first leg, the system continuously
   evaluates whether acquiring the complement creates superior
   risk-adjusted value.
4. If the complement can be acquired such that all-in pair economics
   exceed the relevant alternative after fees/rebates/execution costs,
   pair formation stays available.
5. Completed complementary inventory is merged/redeemed/recycled when
   economically appropriate.
6. Pair economics stay **separate** from directional economics and from
   residual inventory economics in attribution.

### Required separate ledgers — never blended into a headline ROI

```
DIRECTIONAL_ENTRY_PNL
PAIR_COMPLETION_PNL
RESIDUAL_INVENTORY_PNL
EXIT_PNL
FEES
MAKER_REBATES
OTHER_VERIFIED_REWARDS
TOTAL_NET_PNL
```

---

## 2. BETTOR IS MAKER-FIRST — BUT NOT MAKER-AT-ANY-COST

The objective is not to predict markets correctly. It is to act as a
liquidity provider on as much economically justified volume as possible,
capturing, where **verified**: spread, maker rebates, liquidity
incentives, fair-value edge, and pair economics — each separately
attributed.

### Target pipeline

```
FAIR VALUE ENGINE
  -> OPPORTUNITY DETECTION
  -> MAKER QUOTE DECISION
  -> QUEUE / FILL MODEL
  -> INVENTORY
  -> REQUOTE / CANCEL / HOLD DECISION
  -> OPPORTUNISTIC COMPLEMENT / PAIR FORMATION
  -> MERGE / EXIT / HEDGE / SETTLE
  -> CAPITAL RECYCLE
```

**NO TRADE is the default.** Every opportunity compares
`EV_MAKER` vs `EV_TAKER` vs `EV_NO_TRADE` (and eventually
`EV_CROSS_VENUE`), preferring maker only when

```
EXPECTED_MAKER_NET_VALUE > max(EXPECTED_TAKER_NET_VALUE,
                               EXPECTED_NO_TRADE_VALUE)
```

subject to inventory, risk and capital constraints.

### Maker EV must be built from, where measurable

```
  FAIR_VALUE_EDGE
+ EXPECTED_SPREAD_CAPTURE
+ VERIFIED_MAKER_REBATE
+ VERIFIED_EXPECTED_LIQUIDITY_REWARD
- EXPECTED_ADVERSE_SELECTION
- EXPECTED_INVENTORY_COST
- EXPECTED_RESIDUAL_RISK
- EXPECTED_REPRICING/CANCEL_COST
- OTHER_EXECUTION_COSTS
```

### Prohibitions, binding

- Displayed spread is **not** earned spread.
- A maker rebate counts **only when a fill occurs**.
- Liquidity rewards count only when eligibility **and** expected
  economics are separately verified.
- **TOUCH ≠ FILL.**
- Queue position is not inferred from displayed size unless venue
  mechanics/data support it.
- Passive fill probability does **not** transfer from RN1, ferrari,
  swisstony, homerunhazard or any reference account to BETTOR.
- A rebate must never rationalise a fundamentally negative trade.
- A high maker-fill rate with negative post-fill markout is **FAILURE**.
- Maker/taker selection is an optimisation, never a fixed rule.

### Required attribution, every candidate and every shadow/live report

```
GROSS_DIRECTIONAL_EDGE
GROSS_PAIR_EDGE
SPREAD_CAPTURE
MAKER_REBATES
TAKER_FEES
LIQUIDITY_REWARDS
ADVERSE_SELECTION
RESIDUAL_INVENTORY_PNL
EXIT/HEDGE_COST
OTHER_COSTS
TOTAL_NET_PNL
```

Never combined in a way that prevents determining **why** the system
made or lost money.

### The objective function

```
MAXIMIZE  expected net PnL per unit of capital and time
SUBJECT TO  bounded inventory, bounded residual exposure,
            adverse-selection limits, correlation limits,
            liquidity limits, kill switches
```

Not maker-fill percentage. A low fill rate with excellent economics is
capacity-limited but acceptable; a high fill rate with negative markout
is failure.

---

## 3. The eventual engine must be able to exploit

1. immediately executable structural pairs, when genuinely positive
   all-in;
2. independently positive directional opportunities;
3. opportunistic conversion of existing directional inventory into
   complementary pairs;
4. cross-venue hedging where contract equivalence **and** executable
   economics are proven;
5. **NO TRADE** when none has positive expected net value.

`mirror_live = false`. No orders, no capital, no production activation,
no Track A change, no Phase X re-dispatch.
