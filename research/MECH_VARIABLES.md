# Mechanism variables — evidence classes and coverage

Owner instruction, 2026-09-11. Supersedes the earlier two-way
"independent vs derived" split, which was too coarse.

---

## The three evidence classes

### A — ACCOUNTING / MECHANISM-DERIVED

Derived from the RN1 fills whose economics are being decomposed.

**Valid for**: describing what RN1's realized economics *consist of*.

**Not valid for**: independent causal evidence that the mechanism caused alpha.
These variables share inputs with the P&L identity, so a regression of realized
P&L on them is partly an algebraic restatement, not a test.

### B — EXOGENOUS / CONTEXTUAL

Not mechanically defined by RN1's realized P&L.

**Valid for**: testing *where* the accounting mechanism varies.

### C — EX-POST / NON-DECISION-TIME

Known only after the trade or the event. Kept strictly apart from anything
available at decision time.

---

## The variable table

| variable | class | source table/field | derivation if derived | known at RN1 fill time | population |
|---|---|---|---|---|---|
| matched quantity `M` | **A** | `trades` | `min(qY, qN)` per condition over the window | no — needs both legs | full |
| `dM` per event | **A** | `trades` | `max(min(cy,cn) − min(cy₋₁,cn₋₁), 0)` | partial — only up to that fill | full |
| pair cost | **A** | `trades` | `vY + vN`, each `Σ(sh·px)/Σ(sh)` per leg | no | full |
| matched share | **A** | `trades` | `M / (qY + qN)` | no | full |
| residual inventory | **A** | `trades` | `|qY − qN|` | no | full |
| first-leg side / price | **A** | `trades` | earliest canonical BUY in the condition | yes, at that fill | full |
| completing-leg price | **A** | `trades` | first BUY on the opposite `outcome_index` | yes, at that fill | full |
| inter-leg timing | **A** | `trades.ts` | `t(first opposite leg) − t(first leg)` | no | full |
| entry price band | **A** | `trades.price` | banding of the fill price | yes | full |
| trade size | **A** | `trades.size` | — | yes | full |
| **sport** | **B** | `markets.sport` | — | yes | full |
| **market type** | **B** | `markets.slug` / `title` | slug grammar | yes | full |
| **event identity** | **B** | `markets.event_slug` | — | yes | full |
| **game_start / time-to-event / pregame-vs-live** | **B** | `us_premap.game_start` via mapper bridge | see bridge below | yes *in principle* | **MAPPER-SELECTED** |
| independent venue/book liquidity | **B** | `copy_probes.depth` (his venue), `mirror_shadow` bid/ask (ours, top-of-book) | — | yes | partial, per-fill |
| settlement winner | **C** | `markets.resolved_prices` | — | **no** | full where retained |
| `rn1_maker_taker_ex_post` | **C** | — | inferred from transaction structure | **no** | not currently derived |
| realized condition economics | **C** | `markets.resolved_prices` + `trades` | Ledger A identity | **no** | full where retained |
| detection lane | **—** | `trades.source` | `poll`/`backfill` → venue, else cash | n/a | full |

### Detection lane is not class B

`trades.source` is **a property of OUR ingestion system**, not an exogenous
property of RN1's strategy. It is reported because it drives *our* measurability
(the probe fires on one lane and almost never on the other), and it must never
be used as an explanation of RN1's original economics. A later detection lane
cannot have caused an earlier fill.

---

## The mapper bridge, and the collider problem

The only observed condition → PMUS bridges are our own mapper's output:

    mirror_books(condition_id, us_market_slug)
    mirror_shadow(condition_id, us_market_slug)
    mirror_candidate_refusals(condition_id, us_slug)

`markets` has no `us_market_slug`; `us_premap` has no `condition_id`. There is
no direct join and **none is to be invented**.

Successful mapping depends on sport, market type, naming and our
candidate-generation path. Those are the same things the mechanism analysis
wants to vary over. Therefore **conditioning on `has_us_bridge = true` can
induce relationships that do not exist in the full RN1 population** — a
collider/selection effect, not a sampling nuisance.

Consequently any analysis involving `game_start`, `time-to-event` or
`pregame/live` is labelled:

> **MAPPER_SELECTED_SUBPOPULATION_ANALYSIS**

unless representativeness is *demonstrated*.

**Even if bridged and unbridged look alike on every measured variable, the
bridge is not to be called missing-at-random.** Unmeasured selection can
remain. The purpose of the bridged-vs-unbridged comparison is to establish
*whether coverage is selected* — never to control the selection away and
declare representativeness.

---

## Denominators — defined once

Window throughout: `ts >= 2026-08-05 00:00Z AND ts < 2026-09-11 12:00Z`,
canonical RN1 fills (cross-feed deduped), `outcome_index IN (0,1)`.

| name | definition |
|---|---|
| `conditions` | distinct `condition_id` with ≥1 canonical fill in the window |
| `acquisition_cost` / `deployed` | `Σ(size × price)` over canonical **BUY** fills |
| `matched_cost` | `Σ over conditions of M × (vY + vN)`, where `M = min(qY,qN)` and `vX = Σ(sh·px)/Σ(sh)` on leg X (BUYs) |
| `realized_pnl` | Ledger A cash basis: `Σ(payoutₓ × netqtyₓ) − net_cost`, only where `markets.resolved AND resolved_prices IS NOT NULL`; otherwise **NULL, never 0** |
| `abs_realized_pnl` | `Σ|realized_pnl|` — used for coverage weighting so that gains and losses do not cancel a variable's apparent reach |

**`dm_leg_notional` is NOT matched cost** and is never used as one. It is
`Σ d_m(t)·px(t)` — one leg at one leg's price, ≈ half a pair's cost. Verified
arithmetically: Check A $0.986/pair vs Check B $0.4956/share, and
0.4956 × 2 = 0.991.

---

## Separation of ledgers

**Ledger A reconciliation is not mechanism attribution.**

    MATCHED_PAIR_PNL + DIRECTIONAL_RESIDUAL_PNL = TRADING_PNL   (exactly)

Explicit cash rebates/rewards are added only where independently observed and
not already embedded in prices.

Mechanism analysis then asks **where those components are large or small**. It
does not permit a regression on class-A (fill-derived) variables to masquerade
as independent proof of causality.

**Ledger B stays separate.** The 3.957% `RN1_VENUE_REPLICATION_DRAG` is a
*copier counterfactual* and does not enter RN1's mechanism reconciliation at
any point.

`mirror_live=false`.
