# Infrastructure request — read-only Kalshi market data for Phase X

**For:** whoever administers the outbound network policy on the hosted
research environment used for BettorToken.
**Requested by:** research (Run 85, Phase X cross-venue study).
**Date:** 2026-09-15.

## What is being asked for

One host added to this environment's outbound allowlist.

| | |
|---|---|
| **HOST** | `api.elections.kalshi.com` |
| **PORT** | `443` (HTTPS) |
| **PURPOSE** | Read Kalshi sports market metadata, settlement rules and order books to test whether PMUS ↔ Kalshi cross-venue arbitrage exists. |
| **HTTP METHODS REQUIRED** | `GET` only |
| **ENDPOINT FAMILIES REQUIRED** | `/trade-api/v2/events`, `/trade-api/v2/markets`, `/trade-api/v2/markets/{ticker}`, `/trade-api/v2/markets/{ticker}/orderbook`, `/trade-api/v2/exchange/status` |
| **AUTH REQUIRED** | **NO** — see below |
| **CREDENTIAL ACCESS REQUESTED** | **NONE** |

Nothing else is requested. Not `trading-api.kalshi.com`, not `kalshi.com`,
not the production API host, not a credential, not a broader rule.

## Why no authentication is needed

BettorToken holds an institutional Kalshi account, but it is not required
for this work and is not being asked for. Our own production client reads
Kalshi market data **unauthenticated**:

- `edge-engine/src/edge/venues/kalshi.py` — `get_book` and
  `_discover_series` call `/markets/{ticker}/orderbook` and `/events` on a
  bare session with no auth headers.
- `backend/sportsassets/api/app.py:1464` — *"Public Kalshi market data (no
  auth needed for market/book reads)."*

The RSA signing path (`KALSHI-ACCESS-KEY` / `-TIMESTAMP` / `-SIGNATURE`,
credentials `EDGE_KALSHI_KEY_ID` / `EDGE_KALSHI_PRIVATE_KEY`) is applied to
`/portfolio/*` only. Phase X reads no portfolio endpoint, so granting the
host grants nothing about the account.

## Why the existing integration cannot serve this instead

The integration is capable; it is unreachable from here. Every relevant
host is refused at the gateway:

```
api.elections.kalshi.com:443       connect_rejected (gateway 403 to CONNECT)
trading-api.kalshi.com:443         connect_rejected
sportsassets-api.onrender.com:443  connect_rejected
www.bettortoken.com:443            connect_rejected
```

So the research container can reach neither the venue nor BettorToken's own
API that already talks to it. No workaround was attempted: not a credential
load, not a production invocation, not a CI tunnel.

## What the added host would be used by

`research/run85_phasex_kalshi.py :: KalshiResearchClient` — a read-only
client written for this purpose, already landed and tested offline.

Its guarantees, each pinned by a test in
`research/test_run85_phasex.py`:

- **GET only.** Any other method raises `ReadOnlyViolation`.
- **Path allowlist.** Only the five endpoint families above. `/portfolio/*`
  is not in the allowlist and `portfolio` is additionally on a denylist, so
  a crafted ticker such as `X/../portfolio/orders` is refused twice over.
- **No credential surface.** The module does not read `os.environ`, does
  not import a crypto library, and contains no credential name. It cannot
  authenticate even if asked to.
- **No write method exists** on the client object — no `place_order`, no
  `cancel_order`, no `post`.
- **Self-limited rate.** 1.0 s minimum interval, exact `Retry-After`,
  exponential backoff that does not snap back, and a sealed receipt for
  every requested observation including the ones that fail. Kalshi's actual
  limit is **not established**; 1.0 s is a self-imposed research floor and
  no result will be described as rate-safe because of it.
- **Production untouched.** This client is separate from the trading
  adapter and changes no production trading rate behaviour.

## Risk

Read-only public market data over HTTPS from one host, at ≤ 1 request per
second, by a client with no write path and no credential access. It cannot
place, modify or cancel an order, move funds, or alter account or
production configuration.

## If this cannot be granted

The alternative is a Kalshi market-data export supplied by BettorToken —
for the candidate sports series, per market: the full `/events` records
with nested markets (including `rules_primary`, `rules_secondary`,
`settlement_sources`, `expiration_time`, `expected_expiration_time`,
`settlement_timer_seconds`), and the full `/markets/{ticker}/orderbook`
payload with every level, captured within seconds of a matching PMUS
capture. The research path ingests that offline with no code change.
