# OBSERVED_PRODUCTION — 2026-09-19

First authenticated read of the Polymarket US **production** institutional
API. Run 3 of `pmx-production`, run id `35455478887`, executed revision
`eb630c5`, 16:36–16:37Z. Read-only: the lane contains no order path.

**This is production evidence and nothing else.** It is not preproduction
evidence, and it says nothing about fills, partial fills, cancels, latency
under load, or profitability — none of which an unfunded account can
establish.

---

## Verdict

| | |
|---|---|
| verdict | `AUTHENTICATED` |
| token mint | 1,184.6 ms |
| token lifetime | **86,400 s** |
| reads attempted / clean | 10 / 7 |

The two-audience correction landed today was **correct**: the assertion
signed `aud = https://pmx-prod.us.auth0.com/oauth/token`, the request field
carried `audience = https://api.prod.polymarketexchange.com`, form-encoded,
and the venue issued a token. Neither value needed a second attempt, and
the historical bare-issuer fallback was never reached.

**The documented token lifetime is wrong in production too.** The docs say
180 seconds and "refresh every 3 minutes"; the venue said 86,400 — the same
figure preproduction returned on 2026-09-10. `token_reuse_window` reads the
response, so nothing depended on the documented number.

## Identity

| | |
|---|---|
| firm | `firms/20260820-bettortokenllc-api-participant` |
| firm type | `FIRM_TYPE_PARTICIPANT` |
| user | returned, masked in logs by the runner |

A **different firm resource** from preproduction's
`firms/20260902-bettortokenllc-api-participant` — separate onboarding,
separate identity, as expected. Dated 2026-08-20 against preprod's
2026-09-02.

## Scopes — eleven, and identical to preproduction's

```
read:marketdata  read:instruments  read:l2marketdata  read:orders
write:orders     read:reports      read:positions     read:dropcopy
read:accounts    write:accounts    read:funding
```

`write:funding` is absent, as in preproduction.

**This answers three of the onboarding questions by observation**, so they
have been struck from the request rather than asked:

- **`read:l2marketdata` IS granted in production.** It is documented as
  premium; we hold it. Commercial terms are still unknown.
- **`read:dropcopy` IS granted.**
- The scope list is **not** assumed to match preproduction — it was read,
  and it happens to match exactly.

**`write:orders` is granted.** The account is entitled to trade. Nothing in
this lane can use that: `pmx_production_read` has no insert, cancel,
replace, preview or funding path, `request_for` refuses any path outside
the read allow-list by name, and a workflow step greps for those paths
before a credential is staged. The entitlement exists; the capability to
exercise it does not.

## Account state — clean and empty

| read | status | result |
|---|---|---|
| `positions` | 200 | **0 positions** |
| `balance` | 200 | **buyingPower "0"** |
| `orders` (report search) | 200 | 0 rows |
| `executions` (report search) | 200 | 0 rows |

Consistent with an account that has been created and not funded. No
orphaned order, no stray position, nothing to reconcile.

## Latency — real numbers, single samples

| read | ms |
|---|---|
| token mint | 1,184.6 |
| `balance` | 84.3 |
| `positions` | 87.9 |
| `executions` | 93.5 |
| `instruments` | 180.5 |
| `orders` | 278.4 |
| `whoami` | 309.1 |
| `users` | **11,161.9** |
| `symbols` | **timed out at 30,016** |

One sample each, from a GitHub runner, unloaded. Not a latency profile.

---

## THREE THINGS THAT DID NOT WORK, and which is whose

### 1. `GET /v1/accounts/accounts` → 403, with `read:accounts` in the token

**This reproduces preproduction exactly** (2026-09-10, same endpoint, same
403, same scope present). Two environments, two credentials, same
behaviour.

That makes it a property of the venue rather than of our configuration,
and it means **the scope list is not the whole permission model** — an
additional per-endpoint or per-resource entitlement applies that the token
does not express. This is now the sharpest open question for Polymarket,
and it is asked with evidence rather than as a guess.

Consequence for us: we cannot enumerate our own accounts through the API.
The trading account resource name has to be supplied out of band, which is
what `PMX_TRADING_ACCOUNT` now does.

### 2. `POST /v1/positions/balances` → 400 — OUR defect

The request shape is wrong: `balance` sends `{account, currency}` and
succeeds; `balances` deliberately dropped `currency` and was refused. The
receipt recorded only "400", which names no cause and suggests no fix.

Fixed: `error_of` now reads the venue's own reason on any non-2xx and puts
it in the receipt as `venueSaid`. A status without the reason beside it is
not evidence.

### 3. `POST /v1/refdata/symbols` → read timeout at 30 s — OUR defect

`users` answered at 11.2 s, so the venue's tail is longer than our 30 s
read timeout. Recording a slow endpoint as `transportError: ReadTimeout` is
a statement about our timeout, not about their service.

Fixed: read timeout raised to 75 s. Whether `symbols` is genuinely slow or
genuinely hung is **not yet established** and will not be claimed until a
run with the longer timeout says so.

---

## The instrument universe — first look, not yet a measurement

`instruments` returned 20 rows with a `nextPageToken`, so the default page
is the first of many. Two observations from that page alone:

- The first symbol is `aaagaspc-usgas-day-2026-09-14-gt4pt16` — **US
  natural gas**, not sport. The production universe is broader than the
  sports board RN1 trades.
- Its state is `INSTRUMENT_STATE_EXPIRED`. The unfiltered listing returns
  expired instruments, so a naive page-one read describes history, not the
  tradable board.

**PREPROD_MARKET_UNIVERSE_OVERLAP cannot be measured from preproduction**
and is now measurable here, but it has NOT been measured. Doing it needs a
state/tradable filter and a full page walk against RN1's actual traded
symbols. That is the next deliverable and it is not yet done. Nothing about
overlap should be inferred from one unfiltered page of twenty expired rows.

---

## What remains unknown

- Production gRPC hostname. `PRODUCTION_GRPC_TARGET = NOT_IDENTIFIED`; the
  documentation spells it four ways and this lane opens no stream.
- Instrument coverage against RN1's traded universe (above).
- Fee and rebate schedule; whether `read:l2marketdata` carries a charge.
- Whether production rate limits differ from preproduction's observed ones.
- Anything about order lifecycle: submit, ack, partial fill, cancel, late
  fill, reconciliation. An unfunded account cannot demonstrate a fill, and
  no order has been sent.

No order. No capital. `mirror_live=false`.
