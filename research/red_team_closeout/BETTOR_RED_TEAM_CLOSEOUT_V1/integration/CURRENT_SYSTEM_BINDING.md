# Current-system binding map

This package is additive. It must bind into the current BETTOR files rather than
create a parallel trading/accounting system.

| Red-team control | Current BETTOR integration target |
|---|---|
| Canonical exposure lock | `allie_capital.py`, capital authority/read models, canonical-claim layer from Kalshi package |
| Two-leg execution sentinel | `agents/adriana_arb.py`, future reviewed execution adapter; SHADOW now |
| Truth-source quorum | `bettor_capital_authority.py`, Audrey reconciliation, venue positions/balances, market-data health |
| Profit-source circuit breakers | `bettor_paper_profitability_stack.py`, profitability OS/scoreboard, strategy lifecycle |
| Venue health isolation | `market_plane/freshness.py`, Kalshi health path, Command market plane |
| Release lineage guard | release/commit guard CI, Render boot markers, production signed readback |
| Fee evidence guard | `venue_selection.py`, `kalshi_orders.py`, dated Polymarket fee schedule |
| Settlement fingerprint guard | `market_plane/settlement.py`, settlement rule registry, Adriana payoff evidence |
| Twin certification gate | `twin/scorecards.py`, Command Twin, capital readiness |
| Independent sample / multiple testing | probability research adapters, profitability validation, Audrey improvement holdouts |
| Capacity frontier | `bettor_paper_profitability_bind.py`, Allie allocation, capital readiness scale trials |
| UI truth/as-of gate | `api/command_capital_readiness.py` and all Command readiness/profitability surfaces |
| Karen false-block economics | `agents/agent_scorecards.py` / revenue reliability adapter |
| Attribution identity | `paper_loss_attribution.py`, profitability stack, agent value-add |
| Credential class guard | market-data identity / PMUS / PMX / Kalshi startup diagnostics |
| Stream currency guard | institutional stream, PMX market plane, Kalshi current-book service |
| Migration guard | migrator / fresh-db tests / commit guard |
| Final readiness | `command_capital_readiness.py`, capital authority; never grants live execution by itself |

## Important current branch finding

`claude/completion-readiness-v1` is currently at
`2bcdf10c8396a6ffa354c74b6a6ee7c574cb931d`.

Do not release it merely because it imported the package. The final integrated
tree must be proven to contain the accepted production lineage and all accepted
successor changes, then all four exact-SHA gates must run on that final tree.

## Existing behavior to preserve

- `workers/all.py` on the completion branch has already removed
  `universal_market_plane` from the shared import list. Do not regress it.
- Adriana remains SHADOW/no authority.
- Kalshi execution isolation remains intact until a separate reviewed release.
- Historical PAPER remains immutable.
- Current negative probability/profitability receipts remain immutable.
- CASH remains a valid/preferred champion.
