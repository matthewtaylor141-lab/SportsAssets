# KALSHI REP — AUTHORITATIVE PRODUCTION CONTRACT (2026-10-07)

Source class: `KALSHI_REP_CONFIRMATION` — the Kalshi representative's direct
confirmation of production semantics, forwarded by the owner (Matt) on
2026-10-07 and recorded here unchanged in substance. It is a NEW source record:
it does not relabel or replace the frozen `KALSHI_ADAPTER_SEMANTICS`
integration cases of the PM Evidence & Acceptance Pack (those stay
`FROZEN_INTEGRATION_CASE`), and it is not a captured production wire payload.
The public documentation pages it points to are captured beside it
(`docs/`, sha256 in `SOURCES.sha256`), with the live series fee-change records
read the same day (`live_series_fee_changes_2026-10-07.jsonl`).

## Market data architecture

1. Primary market data must be authenticated WebSocket order-book
   subscriptions.
2. REST is for initial load and recovery, not the steady-state current-book
   path.
3. Every subscription begins with a snapshot, followed by sequence-numbered
   deltas.
4. On any sequence gap or reconnect: immediately mark affected books
   `GAP/STALE`; do not continue routing from the old book; resubscribe;
   require a fresh snapshot; only then restore `CURRENT`.
5. `GET /trade-api/v2/account/limits` is the authoritative
   account-tier / rate-limit readback. Do not hardcode the default
   200-connection cap as our actual account limit.
6. Expose the actual tier / limits in the Kalshi health / readback.

Current-code correction: `kalshi_market_data.py` (credential-free GET-only
REST) is kept only as bootstrap / recovery / fallback. A dedicated
authenticated Kalshi WebSocket market-data runtime is the primary book
source. Kalshi steady-state book polling never runs on `sportsassets-api`;
the dedicated market-data / market-plane service is preferred.

## YES / NO execution

* In a two-team event each team has its own market; Yankees YES and Rays NO
  are on different markets with separate books, both independently
  executable, and their prices may differ. This remains a valid
  canonical-claim routing opportunity.
* Within ONE market, buying NO is equivalent to selling YES;
  NO ask = 100¢ − best YES bid; YES and NO positions in the same market net;
  the exchange does not hold both sides independently in one market.

Therefore:

1. Same-market YES and NO are not separate liquidity pools.
2. Same-market YES + NO is never structural arbitrage.
3. Same-market YES / NO exposure nets at the market level.
4. Cross-market aliases (Yankees YES / Rays NO) remain separate executable
   routes.
5. Canonical claim identity may collapse economic exposure; quote / book
   identity stays separate by market.

## Fees

* Use the published fee schedule, including series-specific fee multipliers.
* No individual fee arrangements; no account-specific volume-tier discounts.
* Separate published incentive programs may exist.

Therefore: no assumption of undisclosed / custom discounts; the published
schedule is versioned; each route binds to the current series-specific fee
multiplier (and any event-level override); an unknown fee or multiplier makes
the route ineligible; incentive economics only when the program terms are
explicitly captured and applicable.

## Account limits

Once credentials are provisioned: `GET /trade-api/v2/account/limits`, recording
account tier, read limits, write limits, WebSocket / connection limits if
returned, `as_of`, source. Rate pacing derives from the actual current account
limits rather than constants where possible.

## Service / credential boundary

No trading authority is expanded. The authenticated Kalshi market-data service
may hold credentials because WebSocket authentication requires them, but its
code path stays structurally read-only: no order-submit import, no cancel
import, no funding path, no Small Live activation, no capital authority.

KALSHI LIVE MONEY = NOT ACTIVATED · ADRIANA = SHADOW_ONLY · SMALL LIVE = SHADOW

## Where each point is bound (this build)

| Point | Binding |
|---|---|
| WS primary, snapshot + seq deltas, gap/reconnect -> GAP, resubscribe, CURRENT only after a fresh snapshot | `sportsassets/kalshi_ws.py` (`WsBooks`), runtime `workers/kalshi_ws_market_data.py` (dedicated-only) |
| REST = bootstrap / recovery / fallback | `workers/kalshi_market_data.py` polls only books the WS does not hold CURRENT |
| account limits readback, pacing from them | `kalshi_ws.parse_limits`, `kalshi_ws.pacing`, KALSHI_HEALTH `account_limits` |
| same-market YES/NO one pool, nets, never arb | `canonical_claims.same_market`, `agents/adriana_claims` (no same-market pair), `redteam/exposure.net_same_market` |
| cross-market aliases separate routes | `canonical_claims.route_claim` (book identity per market) |
| published schedule x series / event multiplier | `sportsassets/kalshi_fees.py` (`effective_terms`, `taker_fee`), migration 315 `kalshi_fee_terms` |
| read-only boundary | `tests/test_kalshi_ws_market_data.py` (import closure, no submit / cancel) |
