# PRODUCTION ONBOARDING REQUEST — Polymarket US institutional

**To send to the Polymarket onboarding contact. Account-specific items only.**

Everything the public documentation already answers has been removed from
this request. We do not ask for anything we can read ourselves, and we ask
for **no secret material over email** — see "How to deliver" at the end.

---

## Already established from your documentation — please do NOT re-answer

| | |
|---|---|
| Production REST base | `https://api.prod.polymarketexchange.com` |
| Production Auth0 domain | `pmx-prod.us.auth0.com` |
| Production token audience | `https://api.prod.polymarketexchange.com` |
| Auth mechanism | RS256 private-key JWT → Auth0 client-credentials |
| Preprod/production separation | separate credentials and separate onboarding |
| Scope model | 11 named scopes; 403 `permission denied: missing required scope <scope>` |
| gRPC limits | 20 concurrent streams/firm; 100 msg/s ingress per firm (1-min average); egress unlimited |
| Order stream | `polymarket.v1.OrderEntryAPI` / `CreateOrderSubscription(symbols, accounts, snapshot_only)` |
| Order search | `POST /v1/report/orders/search`, `SearchOrdersRequest` |

Our firm today: **BettorToken LLC**, preproduction participant
`firms/20260902-bettortokenllc-api-participant`.

---

## 1. Credential delivery status

1. Has a **production client_id** been issued for BettorToken LLC? If so,
   through which channel will it be delivered?
2. Is our **production RSA public key** registered yet? If not, what is the
   submission procedure and expected turnaround?
3. What **kid** value should the JWT header carry in production?

## 2. Resource names

4. Our production **participant resource name** — `firms/<firm>/users/<user>`.
5. Our production **clearing member and trading account resource names** —
   `firms/<firm>/accounts/<account>`.

## 3. Entitlements actually granted

6. The **exact scope list** granted to our production credential. Our
   preproduction credential carries:
   `read:marketdata read:instruments read:l2marketdata read:orders
   write:orders read:reports read:positions read:dropcopy read:accounts
   write:accounts read:funding`
   — we are **not** assuming production matches it.
7. Specifically: is **`read:l2marketdata`** (documented as premium) granted
   in production, and on what commercial terms?
8. Is **`read:dropcopy`** granted?

*Context for 6–8: in preproduction, `GET /v1/accounts/accounts` returned 403
with a token that carried `read:accounts`. We would like to understand
whether the scope list is the whole permission model or whether additional
per-endpoint entitlement applies.*

## 4. Economics

9. The **institutional fee and rebate schedule** applicable to our account,
   with its effective date. We currently hold only the retail published
   schedule and will not assume it transfers.

## 5. Operational limits

10. Are production **REST rate limits** account-specific or firm-tier? Our
    preproduction observations were: ListInstruments 6/min, GetBBO and
    GetOrderBook 12/min, SearchOrders/SearchExecutions/SearchTrades 12/min,
    100 req/s per firm, 429 body `{"code": 8}`. Please confirm or correct
    for our production tier.

## 6. One documentation inconsistency we cannot resolve

11. **The gRPC hostname.** Your documentation gives four different spellings
    across pages, and this affects preproduction as well as production:

    | spelling | pages |
    |---|---|
    | `grpc-api.{env}.polymarketexchange.com:443` | `/grpc-api/overview`, `/streaming-endpoints/*` |
    | `grpc-{env}.polymarketexchange.com:443` | `/trader-guide/environments`, `/trader-guide/connection-issues` |
    | `grpc.preprod.polymarketexchange.com:443` | `/data-guide/market-data`, `/data-guide/candlestick-data` |
    | `grpc-api.polymarketexchange.com` (no env) | `/changelog` |

    Please confirm the correct target for **both** environments.

## 7. FIX / PrivateLink

12. Is **FIX access with AWS PrivateLink** available to our account? If so:
    entitlement status, commercial terms, and lead time. We are not
    requesting it — we are deciding whether it is justified, and we will not
    make it a prerequisite for our first integration.

---

## How to deliver

- **Identifiers** (client_id, kid, participant and account resource names,
  scope list, fee schedule, rate-limit tier): email is fine.
- **The private key**: we generate the keypair; only the **public** key goes
  to Polymarket. No private key, API secret or bearer token should ever be
  sent to us by email or chat, and none is requested here.
- Once received, the credential holder installs the values directly into the
  repository secret store. They are never pasted into a conversation, a
  ticket, or a pull request.

## What we will do with it

Nothing automatically. Production credentials unlock a **read-only**
production verification (authentication, identity, scopes, balances,
instruments, report search, positions) with no order of any kind. Production
trading remains disabled and requires separate written approval.
