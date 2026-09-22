# Seven-book reconciliation, then the corrected economic findings

No order was placed, cancelled or closed. No production file, trading
control or deployment changed. Branch `claude/bettor-none-pool-fix`,
which no service tracks.

**Request accounting — every outbound call, including failures**

| # | call | result | counts against the 14 |
|---:|---|---|:--:|
| 1–3 | `portfolio.positions`, `open_orders`, `portfolio.activities` via `venue-reconcile` | **all three HTTP 401** | **yes** |
| 4 | `GET /api/desk/accounts` (API service uses its own venue keys) | 200, informative | **yes** |
| 5 | `GET /api/admin/open-orders` | 200, but a **database** read, not a venue read | **yes (conservatively)** |

**5 of 14 spent, 9 remaining.** Requests 1–3 returned no information.
I count them anyway: the SDK raised `AuthenticationError(status_code=401)`,
and I cannot tell from that message alone whether the request left the
runner or was refused client-side. Counting them is the conservative
reading.

**That failure was avoidable and I should have caught it.** The repo
already documents it, in `venue-reconcile-via-api.yml`'s own header:
*"venue-reconcile.yml builds its own PMUS client and needs PMUS_KEY_ID /
PMUS_SECRET_KEY as repository secrets. They are not set, so that lane is
blocked."* I dispatched the blocked lane without reading that first.

---

## 1. The seven books

### Local record

| book | state | market | intent | `ledger_net` | peak (historical) | frozen reason |
|---:|---|---|---|---:|---:|---|
| 838 | closing | aec-wta-cadbra-sartor-2026-09-08 | BUY_LONG | **+32** | 972.16 | venue_ledger_disagree |
| 1075 | closing | aec-wta-ireesc-mirbul-2026-09-09 | BUY_SHORT | **0** | 652.91 | venue_ledger_disagree |
| 1090 | closing | aec-itfme-trimcc-xavpal-2026-09-09 | BUY_SHORT | **0** | 90.33 | — |
| 1200 | **frozen** | astatc-cs2-ice-nemi-2026-09-09-map1 | BUY_SHORT | **−1444** | 867.54 | order_state_unknown |
| 1218 | closing | atc-spl-fat-dir-2026-09-09-dir | BUY_LONG | **0** | 24.33 | — |
| 1426 | closing | aec-itfwo-yuhwan-yufren-2026-09-10 | BUY_LONG | **+1** | 10.38 | — |
| 1581 | closing | astatc-cs2-mibr-bb-2026-09-10-map2 | BUY_SHORT | **0** | 6663.04 | — |

**Peak exposure is historical and I previously reported it as though it
were current.** The $9,280.69 figure is the sum of seven lifetime
high-water marks. Our own ledger says **four of the seven are already
flat**. Only 838 (+32), 1200 (−1444) and 1426 (+1) carry a recorded net.

Two orders never reached a terminal state locally:

| book | venue order id | state | side | qty | wire | placed |
|---:|---|---|---|---:|---:|---|
| 838 | *(none)* | `lost` | SELL_LONG | 32 | 0.41 | 2026-09-08T23:38:31Z |
| 1200 | `CD0NCD3GESK5` | `unknown` | BUY_LONG | 54 | 0.35 | 2026-09-09T18:10:01Z |

Book 1200's row carries `updated_at = 2026-09-22T17:45:51Z` — **minutes
before this query**. Something is still polling it; `sportsassets-workers`
is not suspended. Recorded, not acted on.

### Venue state, 2026-09-22T17:51Z

`GET /api/desk/accounts`, through the API service's own credentials:

```
configured : True        error : None
cash       : 20972.89
open_value : 0.00
account_val: 20972.89
positions  : (empty list)
```

The endpoint's own disambiguation applies: **configured true, no error,
empty positions list ⇒ the venue holds nothing.** Not "the read came
back blank."

### Classification

| book | venue position | resting order | settled? | classification |
|---:|---|---|---|---|
| 838 | **none** | not established | not established | **NO CURRENT EXPOSURE; local record stale** |
| 1075 | **none** | not established | not established | **NO CURRENT EXPOSURE; local record stale** |
| 1090 | **none** | not established | not established | **NO CURRENT EXPOSURE; local record stale** |
| 1200 | **none** | not established | not established | **NO CURRENT EXPOSURE; local record stale** |
| 1218 | **none** | not established | not established | **NO CURRENT EXPOSURE; local record stale** |
| 1426 | **none** | not established | not established | **NO CURRENT EXPOSURE; local record stale** |
| 1581 | **none** | not established | not established | **NO CURRENT EXPOSURE; local record stale** |

**All seven carry zero current venue exposure.** The `closing`/`frozen`
states are stale local records, including the −1,444 on book 1200.

**What is NOT established, and why I am not asserting it:**

1. **Settled vs traded out.** Both end in no position. Distinguishing
   them needs `POSITION_RESOLUTION` activity records, which the blocked
   lane would have supplied.
2. **Resting orders.** `/api/admin/open-orders` is a *database* read of
   `live_orders WHERE whale_username='manual'` — the workflow says so
   itself. It is **not** a venue read, so the two non-terminal orders
   (~$32 notional) are not ruled out at the venue. `open_value: 0.00`
   is suggestive, not conclusive, at that size against a $20,972.89
   balance.

### The precise remaining reads

| # | read | answers | requests |
|---:|---|---|---:|
| R1 | `portfolio.activities`, `POSITION_RESOLUTION`, 2026-09-08→10, 7 slugs | settled vs traded out, with timestamps | ≤ 4 |
| R2 | venue `open_orders` (real venue call) | whether `CD0NCD3GESK5` and book 838's lost order still rest | 1 |
| R3 | `search-executions` for the two order ids | terminal disposition of both | ≤ 2 |

**Blocked on:** `PMUS_KEY_ID` / `PMUS_SECRET_KEY` are not repository
secrets. R1–R3 need either those secrets set, or new read-only endpoints
on the API service. **Setting secrets is a credential movement and is
not something I will do.** 9 of 14 requests remain and are reserved for
R1–R3 the moment a lane exists.

### One correction this produced

`recent_trades` in the same payload shows venue activity through
**2026-09-20T22:52Z** on NFL markets. My earlier statement that "the
lane has been idle 12 days" was true of the **mirror lane** and **false
of the account**, which traded two days ago from another path. That
matters for the residual experiment (§6).

---

## 2. Fee validation, narrowed

### The 205 are explained — by the published multi-fill rule

A per-execution test cannot see an order-level cap. Grouping the 2,940
aggressor executions into their 1,929 orders:

| difference from order-level cap | orders | executions |
|---|---:|---:|
| **0.0000** | 1,848 | 2,686 |
| −0.0100 | 77 | 222 |
| −0.0200 | 4 | 32 |

**Every difference is negative.** The charge is never above
`banker(0.06 × Σ n·p·(1−p))`. That is the published rule exactly: the
multi-fill adjustment *may only reduce*.

On the **201 orders where the two candidate rules disagree**:

| | orders |
|---|---:|
| venue matched the **order-level cap** | **134** |
| venue matched the **per-fill sum** | 66 |
| neither | 1 |

So neither rule alone reproduces every order. The cap is a true upper
bound (never violated), the per-fill sum is not, and 66 orders land on
the per-fill value where it is the lower of the two. **Our engine's
per-order cap is correct as a bound and imprecise as a predictor on
~4% of multi-fill orders, by 1–2 cents.**

### Data completeness

| | |
|---|---:|
| executions in receipts | 15,592 |
| of which non-fills (`NEW`, `EXPIRED`, zero shares) | **12,307** |
| actual fills used for validation | **3,285** |
| distinct orders in executions | 10,862 |
| orders with a venue `order_id` | 10,865 |
| **orders with an id but no execution record** | **3** |

The "15,592 executions" I quoted is the *message* count; the fee
validation rests on **3,285 fills**. Three acknowledged orders have no
execution record at all — a small completeness gap, not investigated.

### The narrowed claim

| coefficient | validated? | evidence |
|---|---|---|
| Θ_maker = −0.0125 | **yes** | 345 fills, pooled −0.012495, exact to the cent on all 345 |
| Θ_taker = **0.06** (JUL2026) | **yes** | 2,940 fills, pooled 0.059971 |
| multi-fill order-level cap | **as an upper bound**, yes | never exceeded on 1,929 orders |
| **Θ_taker = 0.0695 (SEP2026)** | **NO** | **0 executions after 2026-09-17T04:00Z** |

Every fill in this history is 2026-09-06..10, entirely inside the July
regime. **The September coefficient our replay now uses is unvalidated
against any real charge**, and validating July does not validate
September. Any forward result priced at 0.0695 carries that gap.

---

## 3. Incentives — my conclusion was wrong

I wrote that the $1.00 minimum "makes the M1–M3 scale ineligible by
construction." **It does not follow.** The payout is

```
(our qualifying score / total qualifying score) × the period's pool
```

and the floor applies to that product. A small order in a small pool can
clear a dollar; a large order in a crowded one can fail to. Size enters
only through *share*.

**Share needed to clear $1.00:**

| pool per event-period | share needed |
|---:|---:|
| $50 | 2.00% |
| $200 | 0.50% |
| $1,000 | 0.10% |
| $5,000 | 0.02% |
| $25,000 | 0.004% |

**What the M-scale would actually need:** 4 contracts at 0.51 = **$2.04**
committed; one 6-hour pre-game period = 12.24 capital-hours. $1.00 is
**49% of the committed stake in one period**. That is an implausible
regime, not an impossible one — and it is a statement about the *ratio*,
not about four contracts.

**C4, in dollars first:**

| | |
|---|---:|
| replay trading result over the window | **−$10.23** |
| reward needed to reach zero | **$10.23** |
| per day | **$1.46** |
| per committed capital-hour | $0.000203 |

**$1.46 a day.** My earlier "165–855% annualised" is the same fact
divided by a small committed base, and it made a $1.46/day shortfall
sound structurally impossible. Dollars and capital-hours lead; the
percentage is secondary.

**Still unretrieved:** pool size per period, the exact scoring formula,
the per-person cap, competing liquidity. Whether $1.46/day is reachable
is **unknown**, not ruled out.

---

## 4. The failed transfer, qualified

Preserved — and its certainty was overstated. Recomputed with the
**market as the cluster**:

| | independence | market-clustered |
|---|---:|---:|
| standard error | 27.29 | **41.13** |
| **z** | **−7.34** | **−4.87** |
| design effect | — | **2.27** |

469 markets, 3,861 orders, 1,099 observed against 1,299.3 predicted.
Clustering inflates the error by √2.27 ≈ 1.51×. The direction survives;
the *certainty* does not.

*(My earlier −6.82 used a cruder variance; −7.34 is the correct
independence figure. Both overstate it.)*

**By exposure band** — the error is not uniform drift:

| band | orders | observed | predicted | error | obs | pred |
|---|---:|---:|---:|---:|---:|---:|
| under 30 s | 929 | 202 | 153.1 | **+48.9** | 21.7% | 16.5% |
| 30–120 s | 1,520 | 493 | 653.7 | **−160.7** | 32.4% | 43.0% |
| 2–10 min | 890 | 355 | 441.6 | −86.6 | 39.9% | 49.6% |
| over 10 min | 522 | 49 | 50.9 | −1.9 | 9.4% | 9.8% |

The model **under**-predicts short exposures and **over**-predicts
medium ones — a shape error, correctable by refitting.

**By market size** — the error concentrates:

| orders per market | markets | orders | error |
|---|---:|---:|---:|
| 1 | 65 | 65 | +14.4 |
| 2–3 | 86 | 213 | +8.8 |
| 4–10 | 197 | 1,275 | −13.0 |
| **11–30** | **110** | **1,774** | **−177.0** |
| 31+ | 11 | 534 | −33.6 |

**88% of the total error sits in the 11–30-orders-per-market band.** One
market (`aec-itfme-dannun-andsou-2026-09-09`, 136 orders) contributes
−13.2 alone.

**What this is:** evidence that *this* model, fit *this* way, on *these*
three days, needs recalibration — with a diagnosable shape error and a
diagnosable concentration. **It is not a universal conclusion that fills
are unpredictable.** I previously wrote it as though it were.

---

## 5. Scope of the four negative replay results

The negatives in `EXECUTION_CALIBRATION.md` §5 hold **only** for: C0, C2,
C3, C4 as declared; four queue-ahead fractions; the tape-backed fill
model; 12 markets over 2026-09-13..20; 100 contracts per leg; and
Θ_taker = 0.0695, which §2 shows is **unvalidated**. They are not a
statement about market making on this venue.

**The two positive markets are not promoted.** A flow-based selection
rule has been written down and **frozen before evaluation** in
`FROZEN_SELECTION_RULE.md`, with its thresholds fixed, its test defined
as fresh chronological data outside the used capture, and an explicit
statement that +$7.11 across two football games is inside the noise.

---

## 6. M4 stays unexecuted — and needs redefining

**Execution reports can establish fills and fees. They cannot establish
reusable buying power.** §2 shows executions carry price, shares,
aggressor and commission — and nothing about balance or collateral.

Two things changed what M4 must measure:

1. **The account traded on 2026-09-20**, two days ago, not twelve. There
   may be recent position-changing events to bracket — but bracketing
   needs *balance snapshots around them*, and no schema table records
   balance. History still cannot answer it.
2. **`/api/desk/accounts` returns `cash`, `open_value` and
   `account_value`.** That is a balance surface we already have
   read-only access to, through a lane that works. It was not available
   to the earlier M4 design.

So the cheaper experiment is now: **poll `/api/desk/accounts` around an
existing position-changing event** rather than create one. That needs an
event at a known time — which is the part history does not supply for
the mirror lane and which I have not established for the 09-20 activity.

**M4 remains specified and unexecuted** ($4.08 peak, $2.11 maximum loss,
≤12 requests, 30-minute cap). Before it is worth approving, the
available read-only reconciliation should finish: R1–R3 above, plus
whether the 09-20 trades have identifiable timestamps that
`/api/desk/accounts` could have been polled around. **I have not
completed that, because the lane is blocked on secrets I will not move.**
