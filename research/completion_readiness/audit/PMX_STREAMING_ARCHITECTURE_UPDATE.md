# PMX Streaming Architecture Update - 2026-10-07

Source: direct Polymarket representative guidance supplied by Matt.

## Correction to prior UMP assumptions

The prior architecture treated `1,000 symbols per stream` as a reason to shard
the active universe across many market-data streams. The venue guidance changes
that conclusion.

## Rep-confirmed limits and behavior

- 20 concurrent gRPC streams per firm.
- The 20 streams are pooled across every gRPC subscription: orders, drop copy,
  positions, balance ledger, RFQ and market data all share the same budget.
- `CreateMarketDataSubscription` accepts up to 1,000 symbols in an explicit
  list.
- But an empty symbol list subscribes to every instrument on a single
  market-data stream.
- For a universe of BETTOR's size, the documented/recommended architecture is
  one empty-list market-data stream, then local filtering.
- `symbols=[]` covers every instrument, not only sports.
- Wildcard market-data updates include price_scale and quantity_scale.
- Depth defaults to 10 and can be set on the request.
- Client-to-server messages are capped at 100/sec per firm, averaged over a
  minute, with short bursts allowed.
- Data sent by Polymarket to BETTOR is unlimited; no published bandwidth cap.
- Market-data streams need only `read:marketdata`.
- KeepAlive is required every 30-60 minutes; otherwise the stream can drop
  after about an hour without client traffic.
- Access tokens last 180 seconds. Refresh in the background and use the new
  token on the next connect; do not cycle a healthy stream just to re-auth.
- `ListInstruments` returns full instrument definitions and pages to 1,000.
  It is capped at 6 calls/minute, so a full ~74k pull takes roughly 12+ minutes.
- Pull the full refdata universe once, cache it, and use
  `CreateInstrumentStateChangeSubscription` for live state changes instead of
  repolling.
- A FIX market data route exists over AWS PrivateLink, market-by-order and up to
  25 depth levels, but it is a separate setup and does not yet push new listings.

## Required BETTOR architecture

1. Dedicated read-only market-plane service.
2. One `symbols=[]` market-data stream for full universe.
3. Local sports/league/market-family filtering.
4. Cached full refdata pull at startup / periodic bounded refresh.
5. Instrument state-change stream for listings/delistings.
6. Shared workers never start the UMP supervisor.
7. The 20-stream firm budget is preserved for other services.

## Completion acceptance

A production implementation must show:

- active UMP runtime is dedicated service, not sportsassets-workers;
- shared worker logs have no `starting loop: universal_market_plane`;
- UMP reports `subscription_mode=SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST`;
- market data stream count for UMP is 1;
- no RESOURCE_EXHAUSTED from stream count;
- refdata bootstrap progress is bounded and cached;
- priority universe / held-position freshness rates are reported separately;
- SMALL LIVE remains SHADOW.
