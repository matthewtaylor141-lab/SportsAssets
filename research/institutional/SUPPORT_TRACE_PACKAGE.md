# VENUE-SUPPORT TRACE PACKAGE — the acknowledged order that no read finds

**Prepared, not sent.** This is the fallback the directive names: *"If the
correct searches cannot reconcile the acknowledged order, prepare a
venue-support trace package rather than sending a new order."* It is filled
in from the `reconcile-order` run's receipts; the sections marked
**[FROM RUN]** are blank until that run happens.

## 1. What we are asking Polymarket

> On <DATE> your preproduction Exchange Gateway answered our order insert
> with HTTP 200 and an `orderId`. We can find no record of that order on any
> read surface we have access to. We are not asking you to act on it — it is
> preproduction and long past. We are asking **which read would have found
> it**, so that our reconciliation is correct before we go to production.

Three specific questions:

1. Is `orderId: CDHM9PJV16R7` a real order in your preproduction system, and
   what is its terminal state?
2. Does `POST /v1/report/orders/search` return orders in that state, with the
   bindings we used — and if not, which endpoint does?
3. Does the search's `clordId` field match the **client-supplied** `clordId`
   on the insert? (We ask because we cannot confirm which value ours was —
   see §3.)

## 2. The acknowledged order

| field | value |
|---|---|
| environment | preproduction (`api.preprod.polymarketexchange.com`) |
| firm | `firms/20260902-bettortokenllc-api-participant` |
| account | `firms/20260902-bettortokenllc-api-clearing-member/accounts/20260902-bettortokenllc-api-account` |
| when | 2026-09-10, ~14:19Z |
| endpoint | `POST /v1/trading/orders` |
| response | `200 {"orderId": "CDHM9PJV16R7"}` |
| instrument | `aec-cs2-all-mgc-2026-09-10` |
| order | 1 contract (`orderQty "100"`, fractionalQtyScale 100), price `"40"` (priceScale 100), GTC |
| submitted `clordId` | **NOT RECOVERED — see §3** |

## 3. The submitted clordId is not known, and CDHM9PJV16R7 is not it

`CDHM9PJV16R7` is the **exchange-returned `orderId`**. It is not our client
id and must not be treated as one.

The adapter mints `clordId = str(uuid.uuid4())` at send time
(`backend/sportsassets/pmx.py:1512`) and holds it only in an in-process memo
(`_intent_by_clord`). The 14:19Z send was made from the `pmx-preprod`
workflow's own inline script, which **did not print or persist the
`clordId`**, and the process is long gone.

So the submitted value is **UNRECOVERABLE from our side**. Three consequences,
stated plainly:

- A search by `clordId` for this order can only be run if Polymarket tells us
  the value, or if a value can be recovered from the run's stored event
  payload (checked: the workflow log prints the request body only at INFO in
  the *library* path, not the inline script's, so it is not there).
- **This is exactly the defect the `venue_clord_id` column (migration 067)
  closes** — the identifier is now written to the durable ledger *before* the
  send, so a future ambiguous send is reconcilable by our own id.
- It is also why `reconcile-order` takes `orderId` and `clordId` as separate
  optional inputs rather than assuming one is the other.

## 4. What we searched, and what came back

**[FROM RUN]** — attach `receipts_<run_id>.json` from the `reconcile-order`
dispatch. It contains, per page: the exact request body, the HTTP status, the
`X-Request-Id`, request/response timestamps, and the identifiers on every row
returned. No credential, bearer, signed assertion or full account payload is
in it.

| query | binding | pages walked | rows | next token remaining | result |
|---|---|---|---|---|---|
| by `orderId` | | | | | |
| by `clordId` | | | | | |

Also previously run (2026-09-10, and **now known to have been run wrong** —
plural and capitalization variants instead of the documented singular
fields): searches by `symbols`, by `orderIds`, with `stateFilter` ALL and
CLOSED. All returned `{"order": [], "nextPageToken": "", "lastExecutions":
[]}`. Those results are reported here as *historical and unreliable*, not as
evidence.

## 5. What we are NOT concluding

- **An empty result is not proof the order was never submitted.** The venue
  acknowledged it with a 200.
- **An empty result is not permission to retry.** No replacement order has
  been sent and none will be under this milestone.
- We are not claiming a venue defect. The likeliest explanation remains that
  we queried incorrectly, which is why the corrected queries come first and
  this package second.

## 6. Contact / handling

- Send through the onboarding contact in the institutional package.
- Attach: this document, `receipts_<run_id>.json`, and the run URL.
- **Do not attach**: the private key, any bearer token, any signed client
  assertion, or full account payloads. The identifiers in §2 are firm and
  account resource names, which support already holds.
