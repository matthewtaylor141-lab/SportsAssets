# BETTOR Kalshi Canonical Venue V1 — Current Repo Alignment

Verified against tested closeout SHA:
`659edaf21c2deb1dcc032932f38a7ff1ec8456c2`

## What already exists and should be preserved

### `sportsassets/kalshi_mapping.py`
This is already high-quality fail-closed mapping logic:
- structured event identity
- structured market family / period / line
- titles are explicitly NOT evidence
- settlement compared as payoff vectors
- unknown settlement states refuse
- three-way / draw behavior can refuse false complements

Do not replace this with fuzzy title matching.

### `sportsassets/venue_selection.py`
Already chooses between eligible venues by net EV after:
- book walk / slippage
- entry fee
- exit cost
- other costs
and refuses an exact economic tie rather than using a venue preference.

The new canonical-claim router should feed this philosophy, not replace it.

### `sportsassets/agents/adriana_arb.py`
Already:
- evaluates fixed-payout structures from payoff vectors
- scans cross-venue pairs
- scans same-venue cross-market pairs
- supports payoff-equivalent leg alternatives
- models fees
- refuses unknown fee schedules
- requires fresh/synchronized books
- is SHADOW_ONLY with no submit/cancel/capital authority

The new layer should provide richer equivalence classes to this engine.

### migration 265
Already has append-only:
- `adriana_arb_scans`
- `adriana_arb_opportunities`
- `adriana_arb_refusals`
with `mode=SHADOW` and `production_effect=NONE`.

Reuse these tables where possible.

### `sportsassets/kalshi_catalogue.py`
Already enumerates open sports series/markets and captures Kalshi rules, but its
own module comments state the production structured candidate-build path is
still missing. That is a primary integration gap.

### `tests/test_kalshi_isolation.py`
Currently proves Kalshi execution is wired into no runner. Preserve this safety
while the canonical venue is introduced in market-data / SHADOW mode.

## The exact missing logic this package adds

The system needs an economic-claim layer above raw venue contracts.

For a two-way MLB game, these may all be the SAME claim:

- Polymarket Yankees YES
- Kalshi Yankees YES
- Kalshi Rays NO

But only when the full payoff vector proves equality across every canonical
state including void/postponement.

The economic identity is therefore:

`canonical event + family + period + full payoff vector`

NOT:

`venue + ticker + side`

and NOT:

`title similarity`.

This also prevents false mappings. In a three-way soccer market:

`Chelsea NO`

usually pays on:

`Arsenal win OR draw`

so it is NOT the same claim as:

`Arsenal YES`.

## Preferred routing rule

For any desired claim, collect every PROVEN equivalent executable instrument
across all venues/sides and choose the minimum TOTAL all-in cost for the exact
quantity:

`principal + fees + slippage/depth + other known execution costs`

Displayed price alone never wins.

The router may split quantity across equivalent instruments when that lowers
total all-in acquisition cost and every book is current.

## Arbitrage topology

Adriana must search both:

- SAME_VENUE
  - Polymarket Yankees YES + Polymarket Rays YES
  - Kalshi Yankees YES + Kalshi Rays YES
  - or equivalent explicit-NO aliases where cheaper
- CROSS_VENUE
  - cheapest proven Yankees claim on either venue
  - cheapest proven Rays claim on either venue

The arb engine should operate on CLAIM CLASSES, not raw contract labels.

## Critical NO rule

Do not synthesize a NO ask from a YES bid unless the venue/market is explicitly
proven to be a single-instrument book where that transformation is executable.

If Kalshi exposes an explicit executable NO quote, ingest it as its own quote
candidate. If the API represents NO through another wire primitive, the adapter
must prove the transformation from actual venue semantics. Never use `1 - YES`
merely because arithmetic says so.
