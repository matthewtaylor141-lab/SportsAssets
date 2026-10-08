# CLAUDE — INTEGRATE `BETTOR_KALSHI_CANONICAL_VENUE_V1.zip`

This is an implementation directive. Build Kalshi into BETTOR as a first-class
venue and close the structured mapping gap. Do not respond with a plan only.

## Branch

Use a clean branch from the accepted/tested system, or from the new completion-
readiness branch only after its runtime changes are understood:

`claude/kalshi-canonical-venue-v1`

Do not mix unrelated research into p0-closeout.

## Package baseline

Run the package unchanged first.

Expected:
`17/17 tests GREEN`

Then adapt it to the actual current code.

## Current code to preserve

Do not replace the good existing primitives:

- `sportsassets/kalshi_catalogue.py`
- `sportsassets/kalshi_mapping.py`
- `sportsassets/kalshi_public_rules.py`
- `sportsassets/kalshi_venue.py`
- `sportsassets/kalshi_orders.py`
- `sportsassets/kalshi_linkage.py`
- `sportsassets/venue_selection.py`
- `sportsassets/agents/adriana_arb.py`
- `sportsassets/agents/adriana.py`
- migration 265 Adriana append-only tables

The package is a canonical-claim/equivalence layer above them.

# 1 — Make Kalshi a first-class market-data venue

Wire the existing complete/cursor-bounded Kalshi sports catalogue into a
persistent current market-data path.

Required per Kalshi instrument:
- venue ticker / instrument id
- series
- sport
- league
- canonical fixture candidate
- scheduled start
- home/away or participants as structured evidence
- market family
- period
- line
- proposition subject
- YES executable quote
- NO executable quote where the venue actually exposes one
- depth
- venue timestamp / BETTOR observed timestamp
- fee schedule
- rules_primary / rules_secondary
- settlement evidence state

Venue health is independent:
`KALSHI_HEALTH` must never be blended with `POLYMARKET_HEALTH`.

A Kalshi 429 cannot make Polymarket stale; a Polymarket reconnect cannot make
Kalshi healthy.

# 2 — Highest-standard mapping

Mapping is ESTABLISHED only when all applicable structured facts agree:
- sport
- league
- league-namespaced team/player identities
- event identity
- scheduled start within explicit tolerance
- family
- period
- line
- proposition subject
- settlement payoff vector

Titles are never enough.

No fuzzy name match can enter an execution or arbitrage path.

Ambiguity refuses.

Preserve WNBA/NBA and other same-city collision protection by namespacing team
identity by league.

# 3 — Canonical economic claims

Implement the package's claim fingerprint concept.

An economic claim is identified by:
- canonical event
- family
- period
- exhaustive outcome space
- complete payoff vector

Venue, market id, ticker, side and display title are aliases, not the economic
identity.

This is the required Yankees example:

Polymarket:
- Yankees YES

Kalshi:
- Yankees YES
- Rays NO

If the full payout vectors are identical, all three belong to the SAME claim
class.

Likewise the opposite class can include:
- Polymarket Rays YES
- Kalshi Rays YES
- Yankees NO

Do not hardcode baseball. This must work generically whenever payoff vectors
prove equivalence.

# 4 — Three-way and non-binary protection

Do NOT infer:

`Team B NO == Team A YES`

in a 3-way market.

For example, if draw is possible:
`Chelsea NO = Arsenal OR Draw`

which is not:
`Arsenal YES`.

The payout-vector equality must catch this automatically.

Do the same for:
- pushes
- ties
- voids
- postponements
- overtime differences
- regulation-only markets
- settlement-source differences
- priced settlement differences

# 5 — Explicit YES/NO price preservation

The system must preserve independently executable quote paths.

If Kalshi provides:
- Yankees YES = 0.49
- Rays NO = 0.45

do NOT collapse the prices before routing.

Collapse only the ECONOMIC CLAIM identity.

The quote candidates remain separate.

Therefore BETTOR sees:
`CLAIM: Yankees wins`
with candidate acquisition paths:
- Kalshi Yankees YES @ ...
- Kalshi Rays NO @ ...
- Polymarket Yankees YES @ ...

# 6 — Best available price is mandatory

For every desired claim, use the lowest ALL-IN executable cost at the required
size.

Compare:
- displayed ask
- available depth
- VWAP
- taker/maker fee actually applicable
- slippage
- other execution costs
- freshness
- minimum order constraints

The winner is:
`MINIMUM_TOTAL_ALL_IN_COST`

not:
- preferred venue
- first venue found
- nominal displayed price
- Yankees YES by convention

Fees can reverse venue selection.

Use existing `kalshi_orders.fee_for` as the source of Kalshi fee truth. Do not
copy a stale fee formula into production if the live module has moved.

Use the current Polymarket dated fee schedule for Polymarket.

Unknown fee = ineligible, never zero.

# 7 — Fee-aware split routing

If an order is larger than the cheapest instrument's depth, allow the same
economic claim to be acquired across several proven equivalent instruments.

Example:
- 3 Yankees YES @ 44c Kalshi
- 3 Rays NO @ 45c Kalshi
- remaining size Yankees YES @ 46c Polymarket

Optimize TOTAL all-in cost including fee rounding.

Do not force one venue if a split is cheaper.

Bound the optimizer; if the requested quantity exceeds the exact optimization
budget, refuse or use a separately proven optimizer. Do not silently
approximate.

# 8 — Adriana: same-venue AND cross-venue arbitrage

This is mandatory.

Adriana should scan CLAIM CLASSES for complementary payoff vectors.

She must look for:

### Same venue — Polymarket
Example:
- Yankees YES @ 47c
- Rays YES @ 48c
- sum after all costs < $1

### Same venue — Kalshi
Example:
- cheapest Yankees claim may be Rays NO
- cheapest Rays claim may be Yankees NO
- or YES may be cheaper

### Cross venue
Example:
- Yankees claim cheapest on Kalshi
- Rays claim cheapest on Polymarket

The topology is an OUTPUT:
- `SAME_VENUE`
- `CROSS_VENUE`

Never a restriction imposed before price optimization.

# 9 — Pair arithmetic

For exact complement structures, require that payoff vectors sum to exactly
the guaranteed payout in EVERY event state.

Then compute at each candidate size:

`guaranteed_profit =
 guaranteed_payout
 - principal
 - all fees
 - slippage
 - execution buffers
 - other known costs`

Only:
`guaranteed_profit > 0`
may produce:
`GUARANTEED_AFTER_COSTS`.

Everything else is a refusal.

# 10 — Book synchronization

For arb:
- each book must be fresh under its venue SLA
- cross-venue observations must be within the configured skew bound
- same-venue books also need synchronized evidence
- stale cheap quotes do not win
- rate-limit/backoff state is venue-specific

# 11 — Settlement evidence

Use the existing settlement payoff-vector infrastructure.

Required states include the event states needed to prove:
- ordinary win/loss
- draw/tie if possible
- push if possible
- overtime/regulation distinctions
- void/cancel
- postponement windows
- settlement-source differences

If any state is unknown:
`NOT_ESTABLISHED`.

Do not infer settlement equivalence from team labels.

# 12 — Persist claim aliases without duplicating accounting truth

Prefer adding canonical-claim evidence to the existing Market Plane / Adriana
records.

Do not create a second P&L ledger.

At minimum management should be able to read:
- claim fingerprint
- canonical event
- economic payoff vector fingerprint
- every venue alias
- YES/NO side
- mapping status
- settlement status
- current all-in acquisition cost
- chosen route
- reason another quote lost

Adriana's existing append-only opportunity/refusal tables should remain her
system of record.

# 13 — Kalshi execution remains gated

The current repo deliberately proves the Kalshi submit path is not reachable
from runners.

Do not destroy that safety while integrating market data and SHADOW arb.

Initial state:
`KALSHI = MARKET_DATA + MAPPING + SHADOW ROUTING + SHADOW ARB`

Only after exact acceptance should any PAPER or Small Live authority be proposed
through the existing gated release process.

No live-money activation in this workstream.

# 14 — Required acceptance tests

At minimum add tests proving:

1. Yankees YES == Rays NO in a proven 2-way event.
2. Yankees YES != Rays YES.
3. Three-way Team B NO != Team A YES.
4. Kalshi Yankees YES vs Kalshi Rays NO: cheaper all-in alias wins.
5. Display price can lose after fees.
6. Stale cheaper quote loses.
7. Insufficient depth causes safe split / refusal.
8. Same-venue Kalshi arb is discovered.
9. Same-venue Polymarket arb is discovered.
10. Cross-venue arb is discovered.
11. Arb can use explicit NO as the cheapest alias.
12. Settlement mismatch blocks equivalence.
13. Unknown settlement blocks equivalence.
14. period mismatch blocks mapping.
15. line mismatch blocks mapping.
16. league/team namespace prevents NBA/WNBA collision.
17. ambiguous fixture mapping refuses.
18. fee unknown refuses.
19. book skew outside threshold refuses arb.
20. all-in guaranteed profit <= 0 refuses.
21. Kalshi 429 state does not contaminate Polymarket health.
22. Polymarket health does not contaminate Kalshi health.
23. Adriana remains SHADOW_ONLY / no submit/cancel/capital.
24. current Kalshi execution-isolation tests remain GREEN.

# 15 — Command Center

Add a venue / claims surface showing:

`Canonical claim: Yankees win`

Available acquisition paths:
- Kalshi · Yankees YES · ask · fee · all-in
- Kalshi · Rays NO · ask · fee · all-in
- Polymarket · Yankees YES · ask · fee · all-in

Highlight:
`BEST ALL-IN ROUTE`

For an arb:
- leg A claim
- chosen venue/side/instrument
- leg B claim
- chosen venue/side/instrument
- SAME_VENUE / CROSS_VENUE
- raw pair cost
- fees
- buffers
- guaranteed payout
- guaranteed net profit
- executable quantity
- book ages/skew
- settlement proof

# 16 — Required checkpoint

Return implementation proof, not a plan:

1. NEW SHA
2. branch/worktree
3. exact files changed
4. package baseline 17/17
5. new canonical-claim tests
6. current Kalshi mapping tests
7. current Kalshi venue tests
8. current Kalshi execution-isolation tests
9. Adriana arb tests
10. settlement tests
11. same-venue arb receipt
12. cross-venue arb receipt
13. YES/NO equivalence receipt using real Kalshi examples
14. venue-by-venue mapping coverage
15. Kalshi current-book freshness receipt
16. 429/backoff receipt
17. exact-SHA backend-tests
18. capital-critical
19. commit-guard
20. engine-diagnostic
21. production SHADOW readback
22. explicit:
   - KALSHI LIVE MONEY NOT ACTIVATED
   - ADRIANA SHADOW ONLY
   - NO AUTHORITY EXPANDED
   - HISTORICAL PAPER UNCHANGED

Do not call Kalshi integrated merely because credentials exist. It is integrated
when the complete sports universe is mapped, priced, settlement-normalized,
visible to the agents, and producing real SHADOW routing/arbitrage evidence.
