# Integration contract

## Inputs from current BETTOR

The adapter that binds this package to the real repo should consume only
structured facts already produced or provable by:

- `kalshi_catalogue`
- `kalshi_public_rules`
- `kalshi_mapping`
- current Polymarket Market Plane
- settlement rule registry
- current book readers
- current fee modules

It should not parse titles in the money path.

## Kalshi YES/NO ingestion

For each Kalshi binary proposition, preserve BOTH executable acquisition paths
when the venue/API exposes them:

- proposition YES
- proposition NO

Do not replace NO with `1 - YES` unless the current venue protocol proves that
is the actual executable representation for that exact market.

Each side becomes a `VenueInstrument` with a full payoff vector.

## Claim aliases

After fixture, market and settlement proof:
- compute claim fingerprint
- group equal fingerprints
- persist aliases/readback

Expected MLB example:
`NYY YES`, `TB NO`, and PMUS `NYY YES` can share one fingerprint.

## Routing

For any BUY of a claim:
1. enumerate all current aliases
2. reject stale / unmapped / unsettled / fee-unknown paths
3. walk depth
4. compute total all-in cost
5. choose minimum-cost path or exact fee-aware split

## Adriana

Convert each ClaimClass to the existing `adriana_arb.Contract` alternative-leg
form. Preserve the current engine's payoff validation and append-only records.

Do not fork a second P&L or arb ledger.
