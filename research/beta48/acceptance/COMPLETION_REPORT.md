# BETTOR — completion report

**C2 is a development candidate. It is NOT execution-validated, and
after the repairs below it is NOT positive.**

---

## 1. Replay repairs — done, with demonstrations

All three were claimed in the previous report and none was actually
implemented. Each is now in the code and shown working.

### (a) Causal ladder joins

`attach_ladders` used **nearest timestamp**, which routinely picked a
book polled ~2.5 s *after* a row to establish the queue our order faced
*before* it. For a queue at entry that is precisely the information an
entry decision cannot have.

Now: the most recent ladder **at or before** each row, bounded by
`max_age_s = 60`.

```
joined 30,590 / 30,590      median age 2.5 s      max age 34.3 s
no_prior_ladder 0           too_stale 0
DEMONSTRATION: rows whose joined book_time > row_time:  0
```

### (b) Persistent per-order queue

The queue ahead was recomputed from every snapshot, so each new order
arriving at our price re-queued us **behind** it — the opposite of
price-time priority.

Now `q_ahead_same_price` is set **once**, from the causal ladder at
entry, and thereafter only decremented. Quantity at a *strictly better*
price is price priority rather than queue position, so that component
is re-read each interval — a genuinely better order does execute ahead
of us.

Visible consequence: **we now fill at `qfrac = 1.0`** (22 C2 episodes),
which was structurally impossible before, because a persistent queue
drains.

### (c) Snapshot depletion is no longer executed volume

A level shrinks on cancellation and repricing too, and can stay flat
while executions happen under replenishment — so it bounds fills in
neither direction. Depletion now feeds **only the queue advance**,
under three labelled scenarios, and **only traded volume can fill us.**
A cancellation ahead of us moves us up the queue; it cannot buy our
contracts.

```
TRADE_ONLY          only printed volume advances the queue   (pessimistic)
TRADE_PLUS_HALF     trades + half of any excess depletion
TRADE_OR_DEPLETION  max(trades, depletion)                   (optimistic)
```

### The result: C2 does not survive

| candidate | qfrac | episodes | filled | **sum $** |
|---|---:|---:|---:|---:|
| C2 | 0.00 | 343 | 110 | **−126.72** |
| C2 | 0.25 | 374 | 40 | **−120.38** |
| C2 | 1.00 | 386 | 22 | **−110.21** |
| C0 | 0.25 | 791 | 165 | −199.56 |

Across execution scenarios at C2: −121.29 / −118.32 / −119.97
(TRADE_ONLY / PLUS_HALF / OR_DEPLETION at qfrac 0.25).

**The +$15.14 was an artefact of the lookahead join and the
per-snapshot queue.** Not of the fee regime, which moved it by 2 parts
in 1,000.

### Also repaired: ladder-walked exits

Taker exits now walk the real ladder. A 100-contract exit into a
40-contract book fills 40 and leaves **60 unliquidated**, which rides
to settlement. **"Hard-flatten" is a request, not a guarantee.** In
this corpus it never bound — 0 episodes hit an exhausted ladder — which
is a fact about this corpus, not about the mechanism.

---

## 2. Capital — reconciled, with the denominator named

**Answering the question directly: $68.3 was the per-episode MEAN.**
Putting it beside a $15.14 total was the error; dividing one by the
other gives 2,217 bp and means nothing.

Time integral of committed capital, `bettor_capital.py`:

| | C2 @ qfrac 0.25 | C0 @ qfrac 0.25 |
|---|---:|---:|
| episodes / filled | 374 / 40 | 791 / 165 |
| **total profit** | **−120.38** | −199.56 |
| **TOTAL capital-hours** | **27,734.68** | 83,913.26 |
| mean capital-hours per episode | 74.16 | 106.09 |
| mean commitment per episode | $80.61 over 0.890 h | $91.60 over 1.128 h |
| **peak concurrent capital** | **$388.50** | **$1,079.00** |
| inventory-hours (filled episodes) | 51.60 total, 1.042 median | 334.86 total, 1.047 median |
| **return per capital-hour** | **−43.405 bp** | −23.782 bp |

**Overlapping episodes.** Episodes never overlap *within* a market but
run concurrently *across* markets. The integral is additive over
episodes either way; overlap sets the **peak**, which is the funding
requirement. A strategy needing $1,079 standing to earn its integral is
a different business from one needing $388.

**Residual inventory** is tracked separately as inventory-hours: the
span from first fill to flat or settlement.

**This is a replay scenario on development data under a simulated
execution model. It is not a yield, not annualisable, and does not
scale.**

### On `unsettledFunds`

The field's existence does **not** prove proceeds cannot be reused
immediately, and I over-read it. What is established: the venue reports
`currentBalance`, `buyingPower`, `assetAvailable`, `openOrders`,
`unsettledFunds`, `marginRequirement` and `balanceReservation` as
**distinct lines**, and `UserPosition` reports `netPosition` and
`qtyAvailable` separately. That the venue *tracks* these is documented;
what any of them *does* to our buying power is **unmeasured**. D4/D5
below settle it.

---

## 3. Fee boundary — corrected and tested

00:00 Eastern on 2026-09-17 is **04:00:00Z** in EDT (UTC−4), not
03:59Z. Both this engine and `forward/fees_v2.py` had 03:59 — one
minute early, so a fill inside that minute was charged the new
coefficient before it applied.

`REGIME_CUTOVER_EPOCH = 1789617600.0` = `2026-09-17T04:00:00+00:00`.
Tested at the boundary, 1 ms before, 1 s before, and across the
mis-charged minute.

**Cumulative taker rounding is now per actual aggressive order.** The
levels one exit sweeps feed the cap; unrelated orders are never pooled.

```
ONE order over 7 levels @0.50x1:  per-fill -0.1400   capped -0.1200
SEVEN separate 1-contract orders: -0.1400  (unpooled, as required)
```

29 engine tests pass, including all five published fee examples.

---

## 4. Time & Sales — URLs discovered, not guessed

Inspected `/assets/js/time-and-sales.js` (the index's own loader):

```js
const manifestUrl = '/files/time-and-sales/manifest.json';
const response = await fetch(manifestUrl);
...
const cacheBustUrl = file.eTag
  ? `/files/time-and-sales/${file.filename}?v=${file.eTag}`
  : `/files/time-and-sales/${file.filename}`;
```

**Manifest fetched and confirmed real** —
`https://www.polymarketexchange.com/files/time-and-sales/manifest.json`,
entries of the form:

```json
{"filename": "20260921-time-and-sales.csv",
 "size": 223108108,
 "lastModified": "2026-09-22T00:16:17.000Z",
 "eTag": "75155d78d06986d5ff77da6ccb5a4a37",
 "wasModified": false}
```

**One day is 223 MB.** Eight days covering our capture is of order
**1.8 GB** — too large to pull into this container, which changes the
plan: the filter must run **on the runner**, streaming each CSV and
keeping only rows whose `Symbol` matches our 12 slugs.

**Reconciliation required before any join**, none of it done yet:
date coverage vs our 2026-09-13→20 window; `Transaction Time` timezone
and precision against our `local_request_wall_utc`; `Symbol` vs
`marketSlug`; and completeness — total printed quantity per market per
day against the `sharesTraded` deltas we already hold. A mismatch there
invalidates the join rather than being averaged away.

**Aggressor identity stays unknown** — the docs say so explicitly — and
so does our queue position. Real prints remove the multi-price
attribution problem and **nothing else**.

---

## 5. D1–D6 — reviewable

**Public retrieval (no credentials, no account):**

| # | method | URL | body | pagination | retries | requests |
|---|---|---|---|---|---|---|
| D1 | GET | `https://www.polymarketexchange.com/files/time-and-sales/manifest.json` | — | none (single doc) | 2, exponential | **1** |
| D2 | GET | `https://www.polymarketexchange.com/files/time-and-sales/{filename}` for the 8 filenames in D1 matching `2026091[3-9]`/`20260920` | — | none | 2 each | **8** |

D2 runs on the GitHub runner, streams each file, and writes out only
rows matching our 12 slugs. Nothing else is retained.

**Authenticated account reads (our credentials, no order):**

| # | method | endpoint | body | pagination | retries | requests |
|---|---|---|---|---|---|---|
| D3 | GET | `/v1/incentives` | — | follow `nextCursor`, **cap 10 pages** | 2 | **≤10** |
| D4 | GET | `/v1/account/balances` | — | none | 2 | **2** |
| D5 | GET | `/v1/portfolio/positions` | — | follow `nextCursor`, cap 2 | 2 | **2** |

**Preview call, quarantined:**

| # | method | endpoint | body | requests |
|---|---|---|---|---|
| D6 | POST | `/v1/order/preview` | `{"request": {marketSlug, intent: ORDER_INTENT_BUY_LONG, type: ORDER_TYPE_LIMIT, price: <5 ticks below bid>, quantity: 4, tif: TIME_IN_FORCE_GOOD_TILL_CANCEL}}` | **3** |

**Arithmetic: 1 + 8 + 10 + 2 + 2 + 3 = 26.** Retries are inside each
row's cap. Pacing 0.10 req/s, the existing bound.

### Is preview non-executing? — partially verified

From our actual SDK surface:

- `preview` returns `PreviewOrderResponse = {order: Order}`. `create`
  returns `CreateOrderResponse = {id, executions: list[Execution]}`.
  **Preview has no `executions` field at all** — distinct response
  type, structurally.
- The returned `Order` carries `commissionNotionalTotalCollected`,
  `commissionsBasisPoints`, `makerCommissionsBasisPoints`.

**Two conclusions, one of them inconvenient.** The type contract is
strong evidence preview does not execute — but a type contract is not
proof of server behaviour. And **the `Order` type carries no collateral
or buying-power field**, so *preview cannot answer the collateral
question* even if it works perfectly. It answers the **fee** question.

That narrows D6's value and leaves M1 as the only route to collateral.

**Trading controls unchanged throughout:** `bettor_live_observation =
false`, `MAX_CONTRACTS = 0`, production on `ba87076`.

---

## 6. M1–M3 — separate, and not unlocked by D6

**A preview failing to answer a question is a reason to propose an
experiment. It is not permission to place an order.** M1–M3 require
their own capital authorization regardless of what D6 returns.

Sequential, never concurrent. One 2-tick market, mid 0.40–0.60.

**M1 — does a resting order encumber buying power?**
1. `GET /v1/account/balances` → record `buyingPower`, `openOrders`.
2. `POST /v1/orders` — BUY_LONG, LIMIT, **4 contracts**, price **5 ticks below best bid**, GTC.
3. `GET /v1/account/balances` at +5 s and +30 s.
4. `POST /v1/order/{id}/cancel`; poll `GET /v1/order/{id}` to a terminal state.
5. `GET /v1/account/balances` after terminal.

**M2 — cancel→ack latency, and does a fill land inside it?**
1. `POST /v1/orders` — BUY_LONG, LIMIT, **4 contracts**, **at best bid**, GTC.
2. `POST /v1/order/{id}/cancel` immediately; poll `GET /v1/order/{id}` at 1 Hz through `PENDING_CANCEL` to terminal.
3. Record `cumQuantity` at each poll.

**M3 — netting and release.**
1. `POST /v1/orders` — BUY_LONG 4 at ask (taker).
2. `POST /v1/orders` — BUY_SHORT 4 at (1 − bid) (taker).
3. `GET /v1/portfolio/positions` + `/v1/account/balances` hourly to settlement.

### Exposure and loss, including every failure mode

| | M1 | M2 | M3 |
|---|---:|---:|---:|
| notional committed | 4 × ~$0.45 = **$1.80** | 4 × ~$0.50 = **$2.00** | 4 × ask + 4 × (1−bid) ≈ **$4.10** |
| **collateral ceiling** (assume full encumbrance) | **$4.00** | **$4.00** | **$8.00** |
| residual if cleanup fails | ≤4 long | ≤4 long | matched pair, pays exactly $4.00 |
| worst-case position value | $0.00 | $0.00 | $4.00 |
| taker fees, 4 @ 0.50, θ 0.0695 | $0.00 (maker, or unfilled) | $0.00 | 2 × $0.07 = **$0.14** |
| cancellation race | a fill inside the window **is** M2's result; it is kept and closed | same | n/a, both are takers |
| failed cleanup | position rides to settlement, worth $0–$4 | same | pair self-liquidates at $4.00 |
| **maximum loss** | **$4.00** | **$4.00** | **$4.24** |

**Maximum combined exposure $16.00. Maximum combined loss $12.24** —
M1 and M2 fully lost, M3 at its worst including both taker fees.
(My previous $12.20 omitted $0.04 of fee.)

**Not executed. No funded orders. Separate capital authorization
required.**

---

## 7. Status

**No candidate is positive after the repairs.** C2 remains the best
*structure* — narrow entry, at-touch placement, flatten before
expiry — and it returns **−43 bp per capital-hour** on development
data, with the previous positive result explained as a lookahead
artefact.

**The next step is D1–D6, all read-only.** Real prints remove the
largest remaining modelling assumption; D4/D5 settle the capital
question that decides whether any version of this is worth funding.

---

## 8. Reproducing

**SHA: `<pinned by the commit adding this file>`**, branch
`claude/bettor-none-pool-fix`. Production remains `ba87076`.

```bash
python -m pytest research/beta48/test_bettor_policy_ev.py -q   # 29
python research/beta48/bettor_tape.py                          # causal join
python research/beta48/bettor_capital.py                       # capital-hours
python research/beta48/bettor_policy_sweep.py                  # candidates
cd backend && python -m pytest tests/ -q -k bettor             # 1,464
```

Re-run this turn: the 29 engine tests (fee boundary, per-order cap) and
every replay output above. Unrelated suites were not re-run.

**Data status: DEVELOPMENT throughout.**
