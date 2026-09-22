# Package A — account reconciliation + fresh observation

**Nothing here is deployed or armed.** Branch `claude/bettor-none-pool-fix`,
which no Render service tracks. M4 and all trading remain unexecuted.
Two independent parts, reviewable and approvable separately.

---

# PART 1 — ACCOUNT RECONCILIATION

## 1.1 The route diff, exactly

`backend/sportsassets/api/app.py` — **+40 lines, 0 modified, 0 removed.**

```python
@app.get("/api/desk/venue-resting-orders",
         dependencies=[Depends(require_desk)])
async def api_venue_resting_orders() -> dict:
    """Resting orders AT THE VENUE, not the live_orders view."""
    from .reconcile_read import resting_orders
    return resting_orders()


@app.get("/api/desk/venue-activity", dependencies=[Depends(require_desk)])
async def api_venue_activity(since_iso: str | None = None) -> dict:
    """TRADE and POSITION_RESOLUTION activity, paged and bounded."""
    from .reconcile_read import historical_activity
    return historical_activity(since_iso=since_iso)
```

**No new venue capability.** `reconcile_read.resting_orders()` and
`.historical_activity()` already exist, are already exercised by
`backend/tests/test_reconcile_read.py` (31 tests pass), and already run
under the API service's own credentials. The only thing absent was a
door. No new credential, no new dependency, no write path, no change to
any existing route's behaviour.

**Identity needs a third route** (see §1.3), which is **not yet written**
— it is listed as a reviewer decision, not slipped in.

## 1.2 Authentication and redaction

| control | mechanism |
|---|---|
| auth | `Depends(require_desk)` — the identical guard `/api/admin/open-orders` already uses. No new auth path. |
| credential handling | Credentials stay in the API service's environment. `pmus._get_client()` consumes them. **Nothing returns, logs, or copies a credential.** No secret is added to GitHub. |
| account-data redaction | `resting_orders()` returns order rows (ids, slugs, sides, sizes, prices). `historical_activity()` returns trade and resolution rows. **Both are account data.** They must be treated as such: read into the session, never pasted into a public artifact, and summarised in reports by count and verdict. |
| identity redaction | `account_identity()` returns **a verdict and a field name — never the identifier.** That is the whole point of using it. |
| unreadable ≠ empty | Both functions carry `ok`, `complete`, `pages`, `stop_reason`. A bounded walk reports itself. The routes return these verbatim. |

## 1.3 Identity must be resolved BEFORE attribution — the blocker, restated

The 2026-09-22T17:51:18Z read returned *"identity keys present in pm:
NONE — the account cannot be identified from this payload."*

**Every position and order finding is therefore attributed to "an
authenticated account", not to ours.** The existing mechanism resolves
this without exposing anything:

```python
reconcile_read.account_identity(expected)   # reads c.account.balances()
```

| verdict | meaning | what may then be attributed |
|---|---|---|
| `match` | authenticated account **is** the configured one | **everything** — attribution unblocked |
| `mismatch` | it is a **different** account | **nothing.** Reconciliation against our ledger is meaningless. Stop. |
| `no_identity` | the venue exposes no identifying field on `balances` either | **nothing attributed.** The blocker stands and stays a blocker. Reads still happen; every downstream claim keeps the qualifier. |
| `unreadable` / `no_expected` | read failed / nothing configured to compare | resolve before proceeding |

**Gate:** identity is **call 1**. The verdict is recorded on the
reconciliation artifact. If it is not `match`, the remaining reads still
run (they cost the same and the data is still worth holding) but **no
sentence in any report may say "our account"** — it says "the
authenticated account", exactly as this package does.

**Reviewer decision required:** `account_identity()` also needs an HTTP
route, or the verdict must be obtained another way. A third route is
+6 lines in the same shape. **I have not written it**, because it reads
`c.account.balances()` — a surface none of the current routes touch —
and widening the deployed read surface is the reviewer's call, not mine.

## 1.4 Request accounting — the nine, fully allocated

**Spent to date: 5 of 14.** (3 × HTTP 401 on the blocked CI lane,
counted conservatively; `/api/desk/accounts`; `/api/admin/open-orders`.)

| order | call | answers | requests |
|---:|---|---|---:|
| 1 | `account_identity()` → `account.balances()` | **the attribution blocker** | **1** |
| 2 | `venue-resting-orders` → `client.orders.list` | the $32.02: does `CD0NCD3GESK5` still rest? does book 838's order exist at all? | **1** |
| 3 | `venue-activity`, `since_iso=2026-09-07`, POSITION_RESOLUTION + TRADE | settled vs traded out for all seven books, **with timestamps** | **≤4** |
| 4 | `venue-activity` continuation, only if `complete: false` | completes the walk | **≤2** |
| | | **total** | **≤8** |

**≥1 request held in reserve.** Retries count. If call 3 returns
`complete: true` inside 2 pages, total is 4 and five remain.

**Note on order 2's scope:** `resting_orders()` refuses to turn an
absent key into an empty list — that is written into the function. An
empty book from it means an empty book.

## 1.5 Deployment scope

| | |
|---|---|
| service | `sportsassets-api` only (`srv-d9gcv6urnols73ce6er0`) |
| branch it deploys from | `claude/session-njaewf`, `autoDeploy=yes` |
| **what a merge would trigger** | a redeploy of `sportsassets-api` **and** `sportsassets-workers` — both track that branch |
| blast radius | two GET routes added; no existing route's behaviour changes; no worker code path touched |
| rollback | revert the commit; the branch redeploys. The routes are additive, so a revert cannot orphan state. |
| **not included** | no secret added, no env var changed, no trading flag touched, no migration |

## 1.6 Acceptance — reconciliation (data only)

- [ ] `account_identity()` verdict recorded and reported verbatim
- [ ] resting-orders read returns `ok: true` with `complete: true`
- [ ] the two outstanding orders are each classified: **still resting /
      cancelled / filled / never reached the venue**
- [ ] each of the seven books is classified **settled** or **traded out**
      with a `POSITION_RESOLUTION` timestamp, or explicitly **still
      unresolved** with the reason
- [ ] actual request count reported against the ≤8 plan
- [ ] **no claim attributing anything to "our account" unless the
      verdict is `match`**

**This is data acceptance. It establishes nothing about profitability.**

---

# PART 2 — FRESH OBSERVATION

## 2.1 SHA and what it contains

| | |
|---|---|
| **deploy SHA** | pinned in `PACKAGE_A_SHA.txt` beside this file |
| includes | the prepared lifetime repair, commit **`5874d21`** — *"Observation lifetime is not acquisition allowance"*: a spent acquisition allowance closes ACQUISITION and no longer disarms the whole OBSERVATION. Covered by `backend/tests/test_bettor_live_loop.py`. |
| **not** included | no route from Part 1 — the two parts deploy independently |
| protected paths untouched | `edge-engine/src/edge/venues/pmus_stream.py` is **not modified**. BETTOR reads through its own `bettor_market_stream`, a second reader over the same transport with no shared cache. |

## 2.2 Frozen policy — C0, unchanged

```
Policy(name="C0")            # every default
  min_spread_ticks  1        placement        ENGINE
  quote_horizon_s   2400     recovery_wait_s  1200
  recovery          MAKER_THEN_TAKER
  size              100 contracts per leg
  queue_ahead_fraction: evaluated at 0.00 / 0.25 / 0.50 / 1.00
```

**No parameter is tuned during or after collection.** C2 stays retired.
C3 and C4 are not in this run.

## 2.3 Market selection — the frozen rule, unchanged

From `FROZEN_SELECTION_RULE.md`, frozen **2026-09-22T18:05Z**:

```
F1  print_interval_fraction >= 0.15   (trailing 24 h)
F2  median_spread_ticks     <= 2
F3  distinct_print_minutes  >= 60
```

Admission is computed **before** each market is subscribed, from the
trailing window, and the admission decision is stored with its inputs.

## 2.4 Start / end conditions

| | |
|---|---|
| **arm** | one `obs-arm` writing `bettor_live_probe_state` with a fresh `probe_id` and explicit reservations |
| **start** | `bettor_live_observation = true` in `ingestion_state` |
| **end, whichever first** | (a) **≥5 admitted event clusters fully observed** from admission to settlement; (b) **72 h wall clock**; (c) the HTTP or subscription budget in §2.5 is exhausted; (d) `bettor_live_observation = false` |
| **fixed evaluation endpoint** | the run ENDS at (a), (b) or (c). Analysis happens **after** the run stops. No look-at-results-then-extend. |

## 2.5 Subscription and HTTP limits

| limit | value | source |
|---|---:|---|
| markets per subscription | **100** | SDK documentation, verified in `bettor_market_stream` |
| subscriptions | 1 | ≥5 clusters needs far fewer than 100 slugs |
| `BETTOR_PROBE_MAX_RPS` | **0.25** | existing probe control |
| `BETTOR_PROBE_CONCURRENCY` | **1** | existing probe control |
| `BETTOR_LIVE_MAX_CONTRACTS` | **0** | **hard zero. No order can be sized.** |
| HTTP listing/BBO budget | reserved explicitly at arm; the loop refuses past it | `bettor_live_probe_state` |
| WebSocket reconnects | recorded, not capped by policy |

## 2.6 Durable storage, stop controls, rollback

| | |
|---|---|
| storage | `bettor_live_journal` (decisions), `bettor_live_ledger`, `bettor_live_cursor`; stream frames to the capture path already used by `run85_phase2` |
| **stop, authoritative** | one `UPDATE ingestion_state SET value='false' WHERE key='bettor_live_observation'` — effective within `CONTROL_EVERY_S` **in the running process** |
| stop, secondary | `BETTOR_LIVE_LOOP=off`. **Kept as a cheap pre-check only** — a restart does *not* reload the environment on this service; only a deploy does. Never relied on as the prompt control. |
| stop receipt | 45 fields via `render-ops sql obs-stop-verdict`, including `shutdown_verified`, `socket_closes`, `close_latency_s`, `orders_submitted` |
| rollback | revert to the pre-package SHA and redeploy; observation is read-only, so nothing needs unwinding |

## 2.7 Collection adequacy — why 30 s is not enough, and the stream is

**Measured against the policy's own timescales** (from 7,223 real
resting orders):

| quantity | value |
|---|---:|
| median time to fill | **86.3 s** |
| median time to cancellation | **84.9 s** |
| p90 time to fill | 365.0 s |

At **30 s snapshots an order's median lifetime contains ~3
observations.** Queue position is a per-order quantity that changes on
every add, cancel and execution ahead of us. **Three snapshots cannot
observe those events; they can only bound them.** The development
replay's `queue_ahead_fraction` sweep exists precisely because that
bound was all the snapshot data supported.

**The stream supplies what snapshots cannot.** `bettor_market_stream`
carries the payload's declared fields:

```
marketSlug, bids, offers, state, stats, transactTime
```

— **full bid and offer ladders**, the market **state** (so a halt is not
mistaken for a quiet market), and **`transactTime`, the venue's own
clock**, alongside our receipt clock. Two clocks distinguish a slow venue
from a slow consumer; one clock cannot.

**Decision: the stream is primary.** HTTP BBO is a reconciliation
sample, not the record.

### Provenance and gaps — recorded, not assumed

Every stored frame carries: venue `transactTime`, our receipt time,
`state`, the subscription epoch and the reconnect counter. Every
reconnect writes a gap record with its start, end and the slugs affected.
**A gap is a labelled hole, never interpolated.**

### Execution uncertainties that will REMAIN after this collection

Stated now, so no later report can imply they were resolved:

1. **Our own order is not in the book.** The recorded ladder never saw
   our quote. A resting order inside the spread changes what others do.
   **Only real resting orders can close this.**
2. **Intra-price queue position is unobservable.** The venue publishes
   aggregate depth per level, not per-order queues. The
   `queue_ahead_fraction` sweep remains a sweep.
3. **Public prints carry no aggressor.** Time & Sales has four columns
   — no side, no aggressor flag, no counterparty.
4. **Θ_taker = 0.0695 is unvalidated.** Zero executions exist after the
   2026-09-17 cutover. Any taker leg is priced on an unverified
   coefficient.
5. **Hidden and iceberg liquidity**, if the venue supports it, is not
   observable at all.

## 2.8 Statistical protocol — fixed BEFORE collection

| | |
|---|---|
| **primary measure** | **net USD per committed capital-hour**, computed by `bettor_policy_final.summarise` with rebates at the published schedule (validated θ_maker = −0.012495) |
| **independent unit** | **the event cluster** — one sporting event. Not the order, not the episode, not the market-day. Orders inside one event share a book, a cancellation policy and one outcome. |
| **weighting** | **equal weight per cluster.** Capital-hour weighting lets one long-lived market dominate; the development corpus already showed 30 of 44 C4 episodes in two markets. |
| **secondary** | net per contract; fill rate by exposure; gap fraction |
| **minimum coverage** | a cluster counts only if observed from admission to settlement with **stream uptime ≥ 90%** and **no single gap > 120 s** |
| **fixed endpoint** | the run stops on §2.4's conditions. Analysis is run **once**, after. |
| **monitoring** | data-quality only: uptime, gap count, frames/s, admission counts. **Policy parameters are not touched using evaluation outcomes.** |

### Sample size — computed, not asserted

Between-cluster dispersion in the development corpus (8 clusters with
episodes): mean **−0.001106**, **SD 0.002760** $/capital-hour.

Two-sided α = 0.05, power = 0.80:

| effect to detect ($/cap-hr) | clusters needed |
|---:|---:|
| 0.0005 | **239** |
| 0.0010 | **60** |
| 0.0020 | **15** |
| 0.0030 | 7 |
| 0.0050 | 3 |

**With 5 clusters the minimum detectable effect is 0.003457 $/cap-hr —
about 3× the magnitude of the development mean.**

> **Five clusters is a COLLECTION target, not an inference target.** It
> is enough to prove the pipeline, the admission rule and the data
> quality. It **cannot** resolve an effect of the size the development
> work suggests, and no profitability conclusion may be drawn from it.

## 2.9 The 0.834 recalibration — frozen as development-fitted

The decision-time fill model over-predicts held-out fills by a constant
~17%; a single factor of **1,099 / 1,317.5 = 0.834** removes it.

**That factor was fitted on 2026-09-09..10 and is development output.**
It is registered here as a **prospective prediction**, not a repair:

- The model **with 0.834 applied** will be run forward on this
  collection, and its error reported.
- **Reporting the fitted level shift as out-of-sample performance is
  forbidden.** The in-sample residual after applying it is zero by
  construction and means nothing.
- If 0.834 does not hold prospectively, that is the finding. It is not
  re-fitted mid-run.

## 2.10 Incentive accounting — per payout unit, not per average

**The floor applies to each payout, so the distribution across payout
units decides what is paid.** For a gross estimate of $10.23 over the
window:

| gross concentrated in *k* periods | $/period | **actually paid** |
|---:|---:|---:|
| 1 | 10.23 | **10.23** |
| 2 | 5.12 | **10.23** |
| 5 | 2.05 | **10.23** |
| 10 | 1.02 | **10.23** |
| 27 (flat) | 0.38 | **0.00** |

**Inferring zero from the average was wrong.** The same gross pays
everything or nothing depending on concentration. I previously reported
the flat case as the answer; it is only the **worst** case.

**Accounting rule for this run:**

1. Compute **gross reward estimate per payout unit** (event × period),
   using the retrieved period definitions: Early/pre-game (listing →
   6 h before), Day-of (6 h → start), Live (start → settlement), Daily
   (midnight–midnight ET).
2. Apply the **$1.00 floor to each unit separately.**
3. Report **gross** and **expected-to-be-paid** as two numbers, never
   one.
4. **Eligible-market terms only** — no rewards for cancelled or
   postponed events; markets outside the programme score zero.
5. Pool size, scoring formula and per-person cap remain **unretrieved**.
   A gross estimate requires them, so this run **measures qualifying
   exposure per payout unit** and leaves the dollar conversion open.

## 2.11 Acceptance — collection (data only)

- [ ] running SHA matches the pinned SHA
- [ ] `orders_submitted = 0` on the stop receipt; `BETTOR_LIVE_MAX_CONTRACTS = 0`
- [ ] **≥5 admitted clusters** observed admission → settlement
- [ ] stream uptime ≥ 90% per cluster; no gap > 120 s
- [ ] every frame carries venue `transactTime` **and** receipt time
- [ ] every reconnect has a gap record
- [ ] admission inputs (F1/F2/F3) stored per market
- [ ] stop receipt written with `shutdown_verified = true`
- [ ] HTTP requests within the armed reservation

## 2.12 Acceptance — profitability (SEPARATE, and not met by the above)

- [ ] **≥15 clusters** for a 0.0020 $/cap-hr effect, or **≥60** for
      0.0010 — per §2.8
- [ ] equal-weight per-cluster mean with a **cluster-robust** interval
      (development design effect was **2.27–2.53**; assume ≥2)
- [ ] the 0.834 factor's prospective error reported
- [ ] gross and expected-to-be-paid incentive reported separately
- [ ] Θ_taker = 0.0695 validated against real charges, **or** every
      taker-inclusive figure carries the caveat

**A green Part 2 is a working collector. It is not evidence of profit.**

---

# COSTS AND DATES

| item | bound |
|---|---|
| reconciliation venue requests | **≤8 of the 9 remaining** |
| observation venue HTTP | the armed reservation; `MAX_RPS 0.25`, concurrency 1 |
| observation WebSocket | 1 subscription, ≤100 slugs |
| wall clock | ≤72 h per run |
| money at risk | **$0.00.** No order can be sized. |
| deploys | 1 for Part 1 (api + workers redeploy), 1 for Part 2 |

## Earliest defensible dates

| milestone | earliest | why |
|---|---|---|
| reconciliation complete | **2026-09-23** | one deploy, ≤8 reads, same day |
| collection acceptance (5 clusters) | **2026-09-29** | admitted clusters need live events; CFB Sat 09-26, NFL Sun 09-27; settlement + stop + receipt |
| **profitability inference at 0.0020** | **~2026-10-13** | 15 clusters ≈ three event weekends |
| **profitability inference at 0.0010** | **~2026-12** | 60 clusters |

**The earliest date a profitability claim could be made is not
2026-09-29.** That is the collector working. The honest answer for a
capital decision is **mid-October at the coarsest resolvable effect**,
and that effect is already larger than anything the development work
suggests is there.
