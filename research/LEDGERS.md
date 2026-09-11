# Two ledgers — owner instruction, 2026-09-11

Two questions that must never be answered with each other's numbers. This is
downstream of `THREE_REGISTERS.md` and narrower: registers say *whose economics
and on whose venue*; ledgers say *what is being reconciled to what*.

---

## LEDGER A — RN1 REALIZED ECONOMICS

> What actually generated RN1's realized economic result?

Inputs: **RN1 fills, settlement, and independently observed RN1 cash
rewards/fees where available.** Nothing else.

Per condition:

    M = min(qY, qN)

    MATCHED_PAIR_PNL       = M * (1 - vY - vN)
    DIRECTIONAL_RESIDUAL_PNL = settlement value of the shares outside M
                               less their attributable acquisition cost

    TRADING_PNL = MATCHED_PAIR_PNL + DIRECTIONAL_RESIDUAL_PNL

Those two components **must reconcile exactly** to fill/settlement trading P&L,
subject only to rounding and to data exclusions that are named.

Explicit cash flows are treated separately, and only where observed:

    TOTAL_ECONOMIC_PNL = TRADING_PNL
                       + EXPLICIT_REBATES_REWARDS
                       - EXPLICIT_FEES

- If canonical fill prices are **pre-fee rather than cash-effective**, say so
  explicitly and keep fees **outside** the reconciliation until supported.
- If rebates/rewards cannot be allocated to a condition, reconcile them at the
  **highest level at which they are actually identified**. Do not fabricate a
  condition allocation.

### The double-count rule (hard)

**Do not create `PASSIVE_PRICE_IMPROVEMENT` as an additive third source of
realized P&L merely because RN1 was a maker.**

Any benefit from obtaining a better passive fill price is **already embedded**
in `vY`, `vN`, pair cost and residual cost. Adding it again double-counts.

A separately additive liquidity-provision component exists **only** for
independently paid cash rebates/rewards that are not already embedded in price.

Estimating "maker price improvement" requires an explicit counterfactual
taker/aggressive execution price. That is an **attribution study**, not part of
the accounting reconciliation, and it lives outside Ledger A.

---

## LEDGER B — BETTOR / COPIER OPPORTUNITY DECAY

> How much of RN1's opportunity remained available to a copier?

**It must never be used to explain RN1's realized P&L.**

Components stay separate and are never summed into one number:

| # | component | status |
|---|---|---|
| 1 | `RN1_GROSS_EDGE_AT_FILL` | register 1 |
| 2 | `RN1_VENUE_REPLICATION_DRAG` | register 2, measured — see denominator below |
| 3 | `PMUS_CROSS_VENUE_BASIS` / `TOP_OF_BOOK` | partially retained (`mirror_shadow` top-of-book only) |
| 4 | `PMUS_DEPTH_SLIPPAGE` | **UNMEASURED HISTORICALLY** — no per-fill PMUS ladder exists in retained data |
| 5 | `PMUS_FEE_SCHEDULE_ESTIMATE` | schedule estimate, PMUS venue |

**No single combined BETTOR ROI** until the unidentified components are either
measured prospectively or explicitly bounded.

### `RN1_VENUE_REPLICATION_DRAG` — the denominator, stated

Retired: **6.094% combined execution drag**. It summed a Polymarket-measured
drag with a Polymarket US fee schedule across venues. Withdrawn permanently.

The surviving figure, under its exact register-2 label only:

    drag_usd   = (vwap_at_max - rn1_fill_px) * shares_filled
    deployed   = shares_filled * vwap_at_max        <- the REPLICATOR's own cost,
                                                       NOT RN1's notional

    RN1_VENUE_REPLICATION_DRAG = sum(drag_usd) / sum(deployed) = 3.957%

On the fully-measurable side-forced BUY cohort: **27,071 events, deployed
$5,399,109, drag ≈ $213,642**.

Per share, from the same cohort: **p50 1.000c, p75 2.000c, p90 4.000c,
p95 6.280c** (`drag_cents = (vwap_at_max − rn1_fill_px) × 100`).

**Do not call this "edge consumed"** until it is compared against RN1 economics
on the **same cohort** and the **same capital basis**. The denominators differ
today: this one is the replicator's deployed capital at the walked vwap, while
RN1's pair edge is a per-pair margin on his own cost.

---

## SELL side — locked (see THREE_REGISTERS.md)

    gross_parity_long_reference = 1 - rn1_complement_fill_px

A/B/C are **quote-basis categories only**: PMUS best bid above / at / below
that reference. Never profitable / breakeven / unprofitable.

    retained PMUS bid present:        20,054 / 32,847 = 61.05%
    no usable contemporaneous bid:    12,793 / 32,847 = 38.95%

---

`mirror_live=false` throughout. No live trading behaviour changed by any of
this work.
