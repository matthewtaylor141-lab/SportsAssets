# BETTOR_KALSHI_CANONICAL_VENUE_V1

Purpose: make Kalshi a first-class BETTOR venue at the highest mapping standard,
while improving both ordinary venue selection and Adriana's same/cross-venue
arbitrage.

## Core idea

Do not equate contracts by labels. Equate them by full economic payoff.

For a proven two-way event:

- Yankees YES
- Rays NO

may be the SAME economic claim.

Their quote paths remain separate. BETTOR buys whichever is cheaper after fees
and depth.

In a three-way market, Chelsea NO is not Arsenal YES because draw is a state in
which the former pays and the latter does not.

## Modules

- `mapping_contract.py`: exact structured fixture / market mapping.
- `normalization.py`: build explicit YES/NO payoff vectors from proven
  propositions.
- `canonical.py`: canonical claim fingerprints and equivalence classes.
- `quotes.py`: fee-aware all-in routing, including exact split routing.
- `arbitrage.py`: same-venue and cross-venue complement scanning.

## Existing BETTOR architecture this package extends

The tested repo already has strong primitives:
- `kalshi_mapping.py` — structured, payoff-vector settlement comparison.
- `venue_selection.py` — net-EV venue selection after costs.
- `adriana_arb.py` — payoff-based same/cross-venue arb engine.
- `kalshi_catalogue.py` — complete/truncated sports catalogue.
- migration 265 — append-only Adriana SHADOW records.

This package fills the missing canonical-claim layer between those pieces.

## Safety

This package:
- has no network client
- has no credentials
- has no order submission
- has no cancel path
- has no capital authority
- does not mutate PAPER
- does not activate Kalshi live money

## Tests

Baseline: 17 tests.

They cover:
- YES/NO aliasing
- all-in price selection
- fee reversal
- depth splitting
- same-venue Kalshi arb
- same-venue Polymarket arb
- cross-venue arb
- explicit NO use in arb
- stale-book refusal
- settlement-proof refusal
- three-way false-complement protection
- league/team namespace mapping
