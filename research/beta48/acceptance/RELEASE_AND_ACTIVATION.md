# RELEASE AND ACTIVATION PACKAGE

**SHA: `20bdd3426a050d1a4c31b1ce9488ed77c89793ae`**, branch
`claude/session-njaewf`. Restrictions unchanged: `mirror_live=false`,
no orders, no capital activation, production `3349219`, pilot
unactivated.

**NOTHING FROM THIS BRANCH IS RUNNING.** `sportsassets-workers` runs
`python -m sportsassets.workers.all` from the default branch, and
`bettor_live_loop` is not in its `LOOPS` list. The word "running" is
used below only where something actually ran.

---

## 1. WHAT RAN, AND WHAT IT OBSERVED

| run | what it was | result |
|---|---|---|
| `scripts/bettor_live_harness.py` | the **real** stream handler, eligibility, normalizer, engine, shadow ledger and settlement ingestion, driven by 16 real captured books | 16 books → 16 decisions → **16 NO_TRADE**, 5 blockers by name, freshness median **1.0015 s**, residual 0, **0 orders** |
| `research-sql` ×5 | read-only queries against the production read replica | depth shape, traded volume, spreads, clocks, maturity — §5 |
| `bettor-capability-probe` ×2 | authenticated venue reads, proof-before-keys | 4 account reads `AuthenticationError`; public market read `ok` |
| `pmx-preprod` (whoami) | institutional preproduction | failed at **"Stage the key"**, before any network call |
| `fetch-docs` ×3 | venue documentation from the runner | subscription limit, level ordering, environment table |

**The socket has not been opened.** The harness replaces it with a
file, so every record is `REPLAY_DECISION` and none is
`PROSPECTIVE_SHADOW`. What the harness establishes is that the chain
runs on real payloads; what it does not establish is that the socket
connects, that subscriptions are accepted, what the venue's real update
rate is, or whether a book message replaces or deltas.

## 2. THE SINGLE REMAINING ACTION

Everything else is finished. The one action left needs approval:

> **Add `("bettor_live", bettor_live_loop.main)` to `LOOPS` in
> `backend/sportsassets/workers/all.py` and merge to the default
> branch.** Render redeploys `sportsassets-workers` on merge.

### Exact diff

```python
# backend/sportsassets/workers/all.py
 from . import (analytics, bettor_state, chain_listener, copy_sweep,
+               bettor_live_loop,
                dispatcher, edge_marks, institutional_md, ...)

 LOOPS: list[tuple[str, Callable[[], Awaitable[None]]]] = [
     ...
+    ("bettor_live", bettor_live_loop.main),
 ]
```

**No `render.yaml` change.** `sportsassets-workers` already carries
`envVars: *backend_env`, which already holds `PMUS_KEY_ID` (36 chars)
and `PMUS_SECRET_KEY` (88 chars) — verified by `render-ops env-keys`,
which returns key names and lengths only. **No new credential, no new
permission, no credential moved.**

### Configuration

| variable | default | effect |
|---|---|---|
| `BETTOR_LIVE_LOOP` | `on` | `off` stops the loop without a deploy |
| `BETTOR_LIVE_MAX_CONTRACTS` | **`0`** | sizes a **simulated** execution only; zero means none |
| `BETTOR_LIVE_STATE` | unset | JSONL path for decision records |
| `BETTOR_LIVE_OPENING_CASH` | `0` | shadow ledger opening balance |
| `BETTOR_FEE_DATE` | today | which dated schedule applies |

### Pre-merge validation

1. `python3 -m pytest backend/tests/test_bettor_market_stream.py backend/tests/test_bettor_live_read.py backend/tests/test_bettor_shadow_loop.py backend/tests/test_bettor_decision_engine.py backend/tests/test_bettor_observation_adapter.py backend/tests/test_bettor_fee_schedule.py` — **241 passing at this SHA**

   *On the full suite:* 10,163 pass, **387 fail, 99 skip** (22m44s).
   Those 387 sit in **56 files, none of them these six**, and they
   pre-date this work — the same suite failed in bulk before any of it.
   I did not fix them and they are not this delivery's to fix; I am
   reporting the number rather than quoting only the passing subset.
2. `python3 scripts/bettor_live_harness.py` — exit 0, all assertions
3. `python3 scripts/bettor_shadow_demo.py` — exit 0
4. `python3 scripts/bettor_replay_real.py` — exit 0

### Post-deploy validation, in order

1. `render-ops logs sportsassets-workers 10` → `bettor_live_loop: N markets subscribed`
2. Within 60 s: the first counters line — `stream.connected: true`,
   `subscriptions.confirmed > 0`
3. `by_action` is **expected to be `{"NO_TRADE": ...}`**. That is the
   engine working. An execution count above zero with
   `BETTOR_LIVE_MAX_CONTRACTS=0` is a **defect** and a rollback trigger.
4. `freshness.median` — this is the **first measurement of the live
   path's latency**. The capture feed's median source-to-receipt delay
   is 549.6 s; the stream's is unmeasured and is the number that decides
   whether a 10-second bound is reachable at all.

### Rollback

| severity | action | effect |
|---|---|---|
| immediate | `render-ops env-set BETTOR_LIVE_LOOP=off confirm=DO` | loop exits; Render redeploys |
| full | revert the `all.py` commit | loop gone |

It writes **only its own JSONL file**, holds no database handle, and
touches no trading path, no accounting record and no venue order
endpoint. Removing it leaves nothing behind.

### Blast radius

`grep` over `backend/sportsassets`: **no module imports
`bettor_live_loop`, `bettor_market_stream` or `bettor_universe`** other
than the new ones and their tests. Adding the loop changes one list in
`all.py`; every other loop is untouched.

## 3. WHY IT IS NOT ALREADY RUNNING — THE VERIFIED BLOCKER

**Venue credentials exist in the runtime and not in CI.** One fact,
two consequences.

| where | `PMUS_KEY_ID` | `PMUS_SECRET_KEY` | `PMX_PREPROD_*` |
|---|---|---|---|
| `sportsassets-api` / `-workers` env | **36 chars** | **88 chars** | — |
| GitHub Actions secret store | **empty** | **empty** | **empty** |

- The capability probe printed `PMUS_KEY_ID:` and `PMUS_SECRET_KEY:`
  **blank** in its own env block, so `pmus._get_client()` took its
  documented public-only branch and all four account reads returned
  `AuthenticationError`. One cause, four failures.
- `pmx-preprod` failed at the **"Stage the key from the secret store"**
  step, which exits with `SECRET_MISSING: PMX_PREPROD_PRIVATE_KEY_B64
  is not set` before any network call.

So there are exactly **two** ways to exercise the loop against live
data, and **both are outside what I hold**:

1. **Deploy it** to the runtime that already has the credentials — §2,
   needs approval.
2. **Put credentials in CI** — which would be *moving credentials*, and
   the directive forbids it.

I did not attempt either.

## 4. PREPRODUCTION — ENTITLEMENT DETERMINED

**It is a different API surface**, and the distinction the directive
asked me to verify is real:

| | our SDK (`polymarket-us` 0.1.2) | the preprod environment |
|---|---|---|
| host | `api.polymarket.us`, `gateway.polymarket.us` | `api.preprod.polymarketexchange.com` |
| auth | Ed25519 `key_id` / `secret_key` headers | **Auth0 OAuth**, `pmx-preprod.us.auth0.com/oauth/token`, refreshed every 3 minutes |
| transports | REST + WebSocket | REST + gRPC + FIX |

The installed SDK ships **no preprod constant** — only
`GATEWAY_BASE_URL` and `API_BASE_URL`, both production. It accepts base
URLs as constructor arguments but names no preproduction one.

**We hold the entitlement.** `.github/workflows/pmx-preprod.yml` is a
complete institutional preprod lane with `whoami | health | refdata |
bbo | book | positions | open-orders | report-orders | reconcile-order |
order-stream | order-preview`, plus `order` and `cancel` behind
`confirm=DO`. Its secrets are namespaced `PMX_PREPROD_*` precisely so
the preprod lane can never pick up the production `PMX_*` slots.

**THE ENTITLEMENT IS PROVEN, NOT INFERRED.** `pmx-preprod` **run 24
succeeded on 2026-09-10** — the venue accepted our credentials and
returned positions and the USD balance. Runs 25 (2026-09-19) and 26
(today) both failed, and the workflow's own header explains why: the
credentials **used to be `workflow_dispatch` inputs** and were moved
into `PMX_PREPROD_*` secret slots for security, because runs 1–24 had
printed the client id, participant id and key id into the logs. Run 25
died on `base64: invalid input` — "the secret was set but was not
decodable" — and run 26 dies earlier still, on `SECRET_MISSING`.

**So the lane has been non-functional since the migration, and the
cause is an unpopulated secret slot rather than anything about our
access.** The missing thing is the secret, not the entitlement. The exact
requirement: `PMX_PREPROD_CLIENT_ID`, `PMX_PREPROD_PARTICIPANT_ID`,
`PMX_PREPROD_KEY_ID` and `PMX_PREPROD_PRIVATE_KEY_B64` present in the
repository secret store. With those, the execution-lifecycle
demonstrations the directive lists — submission, acknowledgment,
partial fills, cancellation, cancellation races, expiry, disconnect
recovery, restart, reconciliation — run through that existing lane,
against dummy funds, and **that is not blocking live decision-only
observation**, which needs only §2.

**Preproduction results would demonstrate execution behavior, not
production profitability.**

## 5. CAPACITY — FROM MEASURED ACTIVITY

### Definitions, stated once

| term | meaning |
|---|---|
| **executed notional** | Σ price × contracts over every fill, **both sides of a round trip** |
| **position notional** | entries only. A round trip is $X position, $2X executed. |
| **working capital** | position notional ÷ turns per day |
| **participation** | our share of the market's traded notional. The venue counts each trade once; **we can be at most one side of it.** |

### The target, decomposed

| | |
|---|---|
| executed notional/day | $500,000 |
| position notional/day | $250,000 |
| contract executions/day | **1,000,000** (11.57/sec) |
| fills/day at 5 contracts | **200,000** (2.31/sec) |

### Against the measured market

**Measured: $396,361 traded notional/day across all 396 observed
markets** (1,106,312 shares, implied average price $0.3583).

> **$500,000/day requires 126.1% of every dollar traded in the
> universe BETTOR observes.** Above 100% it is not a hard target; it is
> larger than the whole measured market.

| participation | executed notional/day | % of target |
|---|---|---|
| 1% | $3,964 | 0.8% |
| **5%** | **$19,818** | **4.0%** |
| 10% | $39,636 | 7.9% |
| 25% | $99,090 | 19.8% |

At a plausible 5%: **7,927 fills/day** (0.092/sec), **79 fills per
market per day** across 100 markets — against a median observed market
that trades **100 shares in a day, total**.

### Capital cycles

Working capital = $250,000 ÷ turns per day:

| holding period | turns/day | working capital |
|---|---|---|
| held to settlement | 1.0 | **$250,000** |
| 4 hours | 6.0 | $41,667 |
| 1 hour | 24.0 | $10,417 |
| 15 minutes | 96.0 | $2,604 |

**Every row is a scenario.** The repository contains **zero fills**, so
no holding period has ever been measured. And RN1's recycling mechanism
— merge/redeem — **is not available on PMUS**: `bettor_merge` returns
`permitted=False` for both account classes. Without it a completed pair
is held to settlement unless sold, which is the **top row**.

### What this is not

- CPU throughput. The decision path measures 21,917 obs/sec and clears
  the requirement by four orders of magnitude; it is irrelevant.
- A projection. $396,361 is measured; the participation rows are
  arithmetic on it; the capital rows are scenarios and say so.
- The whole venue. 1,526 markets were observed by a research sampler
  not built for coverage. **Expanding the universe is a specific,
  measurable requirement** — and nothing here measures the venue.

## 6. ACCOUNT RECONCILIATION — AND THE SEPARATION

The directive asks for account identity, historical activity and
resting-order reconciliation, and asks to keep **market settlement**
separate from **account cash settlement**. They are separated in code:

| | source | what it means | status |
|---|---|---|---|
| **market settlement** | `/v1/markets/{slug}/settlement` | what the contract paid | endpoint wired, units not assumed |
| **account cash settlement** | `portfolio.activities` (`POSITION_RESOLUTION`), `portfolio.balances` | what **our account** received | reads built, **blocked on the credential** |

A market settling at $1.00 says nothing about whether our account was
credited, in what amount, net of which fees. Collapsing them is how a
model's fee term goes unverified forever.

`reconcile_read.capability_probe()`, `account_identity()`,
`historical_activity()` and `resting_orders()` are built and order-free
(AST-asserted in a step holding no credentials). They ran. They
returned `AuthenticationError` for the reason in §3.

## 7. ON THE CRITICAL PATH

| # | item | blocked by | who can unblock |
|---|---|---|---|
| 1 | live decision-only observation | the §2 merge | owner approval |
| 2 | account identity / activity / resting orders | PMUS secrets absent in CI *or* the §2 deploy | owner |
| 3 | execution lifecycle in preprod | `PMX_PREPROD_*` secrets absent since the 2026-09-19 migration — **entitlement proven by run 24** | owner |
| 4 | `p_fill`, markout, holding period | no resting orders have ever existed | funded pilot |
| 5 | `holds_both_legs_independently`; complement identity | #2, plus **no leg-level venue identifier in the capture** | #2, then a capture change |

**#4 is the genuine circularity.** #1, #2 and #3 are each one owner
action. #5 needs #2 and then a change to what we capture.
