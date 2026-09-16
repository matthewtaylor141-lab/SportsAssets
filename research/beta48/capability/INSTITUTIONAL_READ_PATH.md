# INSTITUTIONAL READ-ONLY MARKET DATA — capability research

Documentation research only. **Nothing was connected.** No credential, no
Auth0 exchange, no gRPC channel, no FIX session, no PrivateLink, no
subscription, no combo, no RFQ, no quote, no order. The §10 forward capture and
`fwd_collect.py` are untouched.

**Provenance.** 339 documentation pages captured 2026-09-16 02:15–02:24Z by
`doc_capture.py`, each stored verbatim with a sha256, on branches
`beta48-capability/docs-35047231583` and `docs-35047389408`. The page list came
from the venue's own `llms.txt` index (330 paths), not from guessing. Every
quotation below is from those stored bytes.

---

## 1. THE HEADLINE

**A venue-enforced read-only market-data credential is documented.** The scope
model is explicit, the enforcement is explicit, and the market-data stream
explicitly does not require the participant identity that the order path does.

But the honest second half: **the gRPC stream that this unlocks is LEVEL_2, not
LEVEL_4.** It does not carry a trade tape with aggressor, and its book entries
have no order identity. The two terms that actually decide the maker case —
`TRADE_AGGRESSOR` and `TRUE_QUEUE_POSITION` — live on the **FIX** market-data
gateway, which needs AWS PrivateLink and exchange onboarding.

So this finding removes the *credential* blocker and only partly removes the
*observability* blocker.

---

## 2. SCOPES — from `/trader-guide/authentication`

> **"Strict scope enforcement.** Calls that are missing a required scope fail
> with 403 Forbidden (REST) / PERMISSION_DENIED (gRPC) and the message
> `permission denied: missing required scope <scope>`."

| scope | documented description |
|---|---|
| `read:marketdata` | BBO and market data subscriptions (including `BiDirectionalStreamMarketData`) |
| `read:l2marketdata` | L2 orderbook depth **(premium)** |
| `read:instruments` | RefData, instrument listings and metadata |
| `write:orders` | Insert / cancel / replace / modify orders; create combo instruments and manage RFQs and quotes |

```
INSTITUTIONAL_READ_MARKETDATA_SCOPE_DOCUMENTED   = YES
INSTITUTIONAL_READ_L2_SCOPE_DOCUMENTED           = YES  (marked "premium")
INSTITUTIONAL_READ_INSTRUMENT_SCOPE_DOCUMENTED   = YES
WRITE_ORDERS_SEPARATE_SCOPE_DOCUMENTED           = YES
```

The endpoint→scope table settles the separation directly:

| endpoint | scope |
|---|---|
| `/v1/orderbook/{symbol}` GET | `read:l2marketdata` |
| `/v1/orderbook/{symbol}/bbo` GET | `read:marketdata` |
| `BiDirectionalStreamMarketData` (gRPC) | `read:marketdata` |
| `CreateMarketDataSubscription` (gRPC) | `read:marketdata` |
| `/v1/trading/orders` POST | `write:orders` |
| `/v1/trading/orders/cancel` POST | `write:orders` |
| `/v1/combos` POST, all `/v1/rfqs` mutations | `write:orders` |

**Every order-creating path requires `write:orders`. No market-data path does.**

### CAN_TOKEN_EXIST_WITH_READ_SCOPES_AND_WITHOUT_WRITE_ORDERS = **YES (model)**

Two facts kept apart, exactly as `PUBLIC_FEE_SCHEDULE` is kept apart from
`BETTOR_TIER_ELIGIBILITY`:

```
SCOPE_MODEL_SUPPORTS_READ_ONLY_TOKEN   = YES   (documented + enforced)
POLYMARKET_WILL_ISSUE_ONE_TO_BETTOR    = NOT_IDENTIFIED
BETTOR_EXISTING_PREPROD_TOKEN_SCOPES   = NOT_IDENTIFIED
```

The third line matters: a preprod institutional credential already exists in
this project (E35, `PMX_CLIENT_ID` / `PMX_PRIVATE_KEY`). **Which scopes it
carries is unknown, and finding out means presenting it — which is a
connection, and is not authorized.** It is not assumed to be read-only, and it
is not assumed to be broad either.

---

## 3. THE gRPC MARKET DATA STREAM — `/streaming-endpoints/market-data-stream`

> **"No Participant ID Required.** This streaming endpoint only requires Auth0
> JWT authentication with `read:marketdata` scope. You do not need to provide
> the `x-participant-id` header **or complete KYC onboarding** to access market
> data streams."

```
MARKET_DATA_STREAM_REQUIRES_PARTICIPANT_ID = NO
AUTH_TYPE          = Auth0 private-key JWT (RS256 client assertion)
                     -> OAuth2 client_credentials access token
                     domains pmx-preprod / pmx-prod .us.auth0.com
                     audience = the API domain
ONBOARDING_REQUIRED          = YES for a Client ID; NO KYC for market data
RETAIL_OR_INSTITUTIONAL      = INSTITUTIONAL
                               ("Individual traders: you do not need to
                                complete this onboarding")
PRODUCTION_ACCESS_REQUIREMENTS = institutional registration portal; signed
                               Entity Participant and Clearing Member
                               Agreement; Client ID issued by Polymarket;
                               separate onboarding per environment
SANDBOX_AVAILABLE   = YES (development, pre-production, production;
                      preprod credentials do not work in production)
SYMBOL_LIMIT_PER_STREAM = 1000   ("create multiple streams" beyond that)
MAX_CONCURRENT_STREAMS  = 20 per firm
                          ingress 100 msg/sec per firm across all streams;
                          egress unlimited
L2_DEPTH_AVAILABLE      = YES (read:l2marketdata; stream `depth`, default 10)
```

Two methods: `CreateMarketDataSubscription` (server-streaming, symbols fixed)
and `BiDirectionalStreamMarketData` (dynamic add/remove). Request carries
`symbols`, `unaggregated`, `depth`, `snapshot_only`. Prices are `int64`,
divided by a per-instrument `price_scale`.

### What the stream does NOT carry

`BookEntry` is **exactly two fields**:

| field | type | description |
|---|---|---|
| `px` | int64 | price (÷ price_scale) |
| `qty` | int64 | **aggregate quantity at this price level** |

No `OrderID`. No per-order timestamp. There is an `unaggregated` request flag
("receive raw order book"), but **the documented response entry has no identity
field either way**, so what an unaggregated stream returns is not established:

```
GRPC_UNAGGREGATED_RESPONSE_SHAPE = NOT_IDENTIFIED
```

Trades appear only as `InstrumentStats.last_trade_px` / `last_trade_qty` — a
last-trade snapshot, **not a tape**, and with no side:

```
TRADE_TAPE_AVAILABLE_ON_GRPC_MD      = NO (last-trade fields only)
TRADE_AGGRESSOR_AVAILABLE_ON_GRPC_MD = NOT_IDENTIFIED
ORDER_LEVEL_PRIORITY_ON_GRPC_MD      = NO
```

---

## 4. FIX MARKET DATA **IS** MARKET-BY-ORDER

From `/institutional/fix-api/fix-market-data-subscription`:

> "…with a corresponding timestamp (**used to determine time priority within a
> price level**). Each order also carries the unique OrderID reference, which
> Participants can use to identify their own orders in market data."

| tag | field | documented meaning |
|---|---|---|
| 37 | `OrderID` | per-order; matches the `ExecutionReport [8]` ack — **lets us find our own order in the book** |
| 278 | `MDEntryID` | unique entry reference (~13-char) |
| 269 | `MDEntryType` | 0=Bid, 1=Offer, **2=Trade**, 4/5/6=Open/Close/Settlement |
| 279 | `MDUpdateAction` | **0=New, 1=Change, 2=Delete** |
| 1003 | `TradeID` | matches `ExecID (17)` on our own fills |
| **2446** | **`AggressorSide`** | **"Only sent for trades. Indicates which side was the aggressor (1=Buy, 2=Sell)"** |
| 264 | `MarketDepth` | **max 25 levels** (0=full book, 1=top) |

Every field the question asked about is present: individual order quantity,
price, OrderID/MDEntryID, order timestamp/time priority, add, cancel/delete,
trade — **and the aggressor side**.

```
FIX_MBO_DATA_LEVEL            = LEVEL_4
QUEUE_POSITION_VALUE          = TRUE_QUEUE_POSITION obtainable, including for
                                OUR OWN resting orders via OrderID
ACCESS_REQUIREMENTS           = institutional onboarding + AWS Account ID at
                                registration + signed agreements
AWS_PRIVATELINK_REQUIRED      = YES (VPC endpoint; Polymarket DevOps must
                                approve the connection request before DNS or
                                FIX connection can proceed)
EXCHANGE_ONBOARDING_REQUIRED  = YES
FASTEST_PATH_FOR_CURRENT_SPRINT = NO
```

One structural detail worth keeping: the venue **exposes two separate FIX
gateways — one for order management, one for market data.** A market-data-only
FIX session is therefore a documented shape, not a favour.

---

## 5. THE DATA QUALITY LADDER

`YES` = directly established. `PROXY_ONLY` = inferable with an assumption that
is not itself verified. `NO` = not obtainable at this level.

| | L0 REST snapshots | L1 streaming BBO | L2 streaming agg. L2 | L3 L2 + trade/aggressor tape | L4 MBO + priority + trades |
|---|---|---|---|---|---|
| TOUCH | YES | YES | YES | YES | YES |
| SPREAD | YES | YES | YES | YES | YES |
| DEPTH | YES | NO | YES | YES | YES |
| DEPTH_DEPLETION | PROXY_ONLY | NO | PROXY_ONLY | YES | YES |
| QUEUE_AHEAD | PROXY_ONLY | NO | PROXY_ONLY | PROXY_ONLY | YES |
| TRADE_AGGRESSOR | NO | NO | NO | YES | YES |
| PASSIVE_FILL | NO | NO | NO | PROXY_ONLY | YES |
| TRUE_QUEUE_POSITION | NO | NO | NO | NO | YES |
| POST_FILL_MARKOUT | NO | PROXY_ONLY | PROXY_ONLY | YES | YES |
| ADVERSE_SELECTION | NO | PROXY_ONLY | PROXY_ONLY | YES | YES |

**Why L2's `DEPTH_DEPLETION` is only a proxy:** a level shrinking between two
book states is consistent with a trade *or* a cancel, and aggregated L2 cannot
separate them. Treating a shrink as a trade would silently manufacture the
touch-rate we are trying to measure.

**Why even L3's `PASSIVE_FILL` is only a proxy:** a trade tape says a passive
order filled at that level; it does not say *whose*. Only L4's OrderID does.

**A proxy must never be promoted.** `QUEUE_AHEAD` at L0/L2 is a *lower bound on
size at or better than our level*, not our position — time priority within a
level is not observable without L4.

Where BETTOR sits and could sit:

```
TODAY                                  LEVEL_0
INSTITUTIONAL gRPC + read:l2marketdata LEVEL_2
FIX MARKET DATA GATEWAY                LEVEL_4
```

---

## 6. WHAT THIS DOES AND DOES NOT SOLVE

The five blocked terms:

| term | L2 (gRPC read-only) | L4 (FIX MBO) |
|---|---|---|
| `BETTOR_PASSIVE_FILL_PROBABILITY` | NO | YES |
| `QUEUE_POSITION` | PROXY_ONLY | YES |
| `ADVERSE_SELECTION` | PROXY_ONLY | YES |
| `TOUCH_TO_TRADE` | PROXY_ONLY | YES |
| `DEPTH_CONSUMPTION` | PROXY_ONLY | YES |

**L2 converts five NOs into one NO and four PROXY_ONLYs.** That is a real
upgrade over polling — continuous book state instead of two-hourly snapshots —
and it is emphatically not the measurement the maker case needs to conclude.
Nothing at L2 can establish that BETTOR's own quote would have filled.

---

## 7. VENUE-ENFORCED LEAST PRIVILEGE BEATS ROUTE C

Route C (our own fixed-function signing broker plus network-path restriction)
was always the weaker argument: it is *containment*, and it fails if our own
code is wrong. A credential the venue refuses to let place an order is a
property of the venue's authorization server, and it holds even if our code is
wrong. The documented 403 / PERMISSION_DENIED path is exactly that.

```
PREFER = VENUE_ENFORCED_SCOPE
ROUTE_C_STATUS = SUPERSEDED_IF_A_READ_ONLY_TOKEN_IS_ISSUED
```

Residual risks, named rather than waved away:

- **The grant is not the model.** A read-only token is documented as
  *possible*; that Polymarket will issue one is `NOT_IDENTIFIED`.
- **`read:l2marketdata` is marked "premium"**, which may carry commercial terms
  that are `NOT_IDENTIFIED`.
- **The existing preprod credential's scopes are unknown** and must not be
  presumed narrow. If it turns out to carry `write:orders`, connecting with it
  would *re-create* the capability we removed.
- **A read-only token still dissolves `COLLECTOR_PHYSICAL_ABSENCE`** as
  currently proved. The forward collector's guarantee is that no code path can
  build an auth header. A second, separate, scope-limited client would need its
  own boundary proof; it must not be bolted into `fwd_collect.py`.

---

## 8. THE CAPABILITY BLOCK

```
SAFE_REALTIME_PATH_FOUND            = YES (documented; not yet granted)
BEST_SAFE_DATA_LEVEL                = LEVEL_2  (streaming aggregated L2 via
                                      institutional gRPC, read scopes only)
READ_ONLY_SCOPE_ENFORCED_BY_VENUE   = YES (403 / PERMISSION_DENIED, strict)
WRITE_ORDER_CAPABILITY_PRESENT      = NO, if the token omits write:orders
                                      NOT_IDENTIFIED for any token we hold
L2_AVAILABLE                        = YES (read:l2marketdata, "premium")
TRADE_AGGRESSOR_AVAILABLE           = NO on gRPC market data
                                      YES on FIX market data (tag 2446)
TRUE_QUEUE_POSITION_AVAILABLE       = NO on gRPC
                                      YES on FIX (OrderID 37 + time priority)
ACCESS_REQUIRED                     = institutional registration portal;
                                      signed Entity Participant and Clearing
                                      Member Agreement; Client ID issued by
                                      Polymarket; per-environment onboarding.
                                      NO KYC and NO x-participant-id for
                                      market-data streams.
                                      FIX additionally: AWS Account ID at
                                      registration + VPC PrivateLink endpoint
                                      approved by Polymarket DevOps.
ESTIMATED_ONBOARDING_COMPLEXITY     = gRPC read-only: MODERATE — a portal
                                      registration, a signed agreement, a
                                      venue-issued Client ID, an RS256 key we
                                      generate. No KYC. No infrastructure.
                                      Days-to-weeks, gated on Polymarket.
                                      FIX: HIGH — the above plus an AWS VPC
                                      PrivateLink endpoint and a manual
                                      DevOps approval step.
EXPECTED_INFORMATION_GAIN           = LEVEL_0 -> LEVEL_2. Converts four of the
                                      five blocked terms from NO to
                                      PROXY_ONLY and none to YES.
                                      LEVEL_4 (FIX) would convert all five to
                                      YES, including the only direct evidence
                                      that OUR OWN quote would have filled.
SHOULD_BETTOR_PURSUE_THIS_ACCESS_NOW = YES — pursue the institutional
                                      read-only gRPC path now, and open the
                                      FIX conversation in the same
                                      registration.
```

**Reasoning for the last field, on engineering information value and access
requirements only.** The gRPC read-only path is cheap, needs no KYC, no
participant id and no infrastructure, and its onboarding latency is
venue-controlled — so the clock starts when we ask, not when we are ready. FIX
is the only path that answers the maker question outright, and it shares the
same registration, so asking about both at once costs nothing extra and
converts a serial dependency into a parallel one.

Against that: L2 alone will not conclude the maker case, so this should not
displace the §10 capture, which is measuring what is measurable today. And the
*whole* premise stays conditional on a grant nobody has made yet.

**This is not authorization to request credentials.** No credential has been
requested, created, installed or presented.
