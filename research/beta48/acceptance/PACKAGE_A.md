# Package A (complete) — reconciliation + execution-model calibration

**Nothing deployed. Nothing armed. No order can be sized
(`BETTOR_LIVE_MAX_CONTRACTS=0`). M4 unexecuted.** Branch
`claude/bettor-none-pool-fix`, tracked by no Render service.

Three parts, approvable separately: **P1 reconciliation**, **P2 fresh
observation**, **P3 incentive retrieval**.

---

# P1 — ACCOUNT RECONCILIATION

## 1.1 Code, complete — the identity gate is now in it

`backend/sportsassets/api/app.py` **+63 lines**, 0 modified, 0 removed.
`backend/sportsassets/config.py` **+10 lines**, additive, inert.

```python
@app.get("/api/desk/venue-identity", dependencies=[Depends(require_desk)])
async def api_venue_identity() -> dict:
    from ..config import settings
    from .reconcile_read import account_identity
    return account_identity(settings().pmus_account_ref or None)


@app.get("/api/desk/venue-resting-orders",
         dependencies=[Depends(require_desk)])
async def api_venue_resting_orders() -> dict:
    from .reconcile_read import resting_orders
    return resting_orders()


@app.get("/api/desk/venue-activity", dependencies=[Depends(require_desk)])
async def api_venue_activity(since_iso: str | None = None) -> dict:
    from .reconcile_read import historical_activity
    return historical_activity(since_iso=since_iso)
```

`config.py` gains one inert field:

```python
pmus_account_ref: str = ""   # owner-set OUT OF BAND; empty -> "no_expected"
```

**Focused checks:** `backend/tests/test_venue_identity_route.py`, 8 tests,
passing. They pin the three properties that make the gate worth having:

| test | pins |
|---|---|
| `test_no_expected_when_nothing_is_configured` | an unset reference **blocks**, never matches |
| `test_the_route_takes_no_caller_supplied_reference` | the route has **zero parameters** — a caller cannot hand it a value that makes the check pass |
| `test_the_route_reads_the_reference_from_settings` | AST check: reads `pmus_account_ref`; must not touch `request`/`body`/`payload`/`resp` |
| `test_match_requires_the_configured_value` | on match, only the **field name** returns |
| `test_a_different_account_is_a_mismatch_and_leaks_nothing` | neither account id nor our reference appears in the output |
| `test_absent_identity_field_is_a_blocker_not_a_pass` | `no_identity` stays a blocker |
| `test_an_unreadable_call_is_not_an_empty_account` | failure ≠ empty |
| `test_the_reference_setting_defaults_to_empty` | no deployment claims a match it has not earned |

## 1.2 The trusted reference — and why the obvious one proves nothing

> *"Matching a value derived from the same response would prove nothing."*

Correct, and worth stating precisely: `account_identity()` extracts
identity-shaped keys from `account.balances()`. If `expected` also came
from that payload it would match by construction. So `expected` comes
**only** from `settings().pmus_account_ref`, and the route takes no
parameter at all.

**There is currently no such reference configured.** `config.py` has
`pmus_key_id`, `pmus_secret_key`, `pm_funder` (a global-CLOB proxy
address, a different venue path) — **nothing identifying the PMUS
account**. So as shipped, the verdict will be **`no_expected`**.

Two ways to make the gate answer something, both named rather than
assumed:

**(a) Owner-supplied reference — the clean one.** The owner reads the
account identifier from the venue's own web UI and sets
`PMUS_ACCOUNT_REF` on `sportsassets-api`. That value never came from the
API response being checked. **This is a reviewer action, not mine** —
it is a configuration change on a production service.

**(b) The order-id anchor — available now, no configuration, and
arguably stronger.** We recorded **10,865 venue-assigned order ids** at
submission time in 2026-09-06..10, in our own database, *before* any
read taken now. If `venue-activity` or `venue-resting-orders` returns
**any** of those ids, the authenticated account is the account that
placed them — only that account can see them. The reference is our
historical record; the test value is today's response; the two sources
are independent.

**(b) needs no new code.** It is a join performed in the analysis step
between the venue read and our `mirror_orders.order_id` column. It is
specified here as **step 5 of the read sequence** below.

**Gate rule:** until **(a) returns `match`** or **(b) finds ≥1 anchor
id**, no sentence in any report says *"our account"*. It says *"the
authenticated account"*, exactly as every finding in
`SEVEN_BOOK_RECONCILIATION.md` and `CLOSEOUT.md` already does.

## 1.3 Redaction

| surface | what leaves the service |
|---|---|
| `venue-identity` | **a verdict and a field name.** Never the identifier, never the reference, never a credential. Asserted by two tests. |
| `venue-resting-orders` | order rows — **account data**. Read into session, summarised by count and verdict in reports, never pasted into a public artifact. |
| `venue-activity` | trade and resolution rows — same treatment. |
| all three | `ok`, `complete`, `pages`, `stop_reason` returned verbatim, so a bounded walk reports itself and cannot be read as an empty book. |

## 1.4 Deployment and read sequence, exactly

**Deploy:** merge the pinned SHA to `claude/session-njaewf`. That
redeploys **`sportsassets-api` and `sportsassets-workers`** (both track
it, `autoDeploy=yes`). Additive routes only; no worker path changes.
Rollback = revert + redeploy; nothing to unwind.

**Read sequence** (5 of 14 spent; **9 remain**):

| step | call | answers | requests |
|---:|---|---|---:|
| 1 | `GET /api/desk/venue-identity` | the attribution gate | **1** |
| 2 | `GET /api/desk/venue-resting-orders` | the $32.02: does `CD0NCD3GESK5` still rest? does book 838's order exist? | **1** |
| 3 | `GET /api/desk/venue-activity?since_iso=2026-09-07` | settled vs traded out for all seven, with timestamps | **≤4** |
| 4 | continuation, **only if** `complete: false` | finishes the walk | **≤2** |
| 5 | *(no request)* join step-3 ids against `mirror_orders.order_id` | **the order-id anchor** | **0** |
| | | **total** | **≤8** |

**≥1 held in reserve. Retries count.**

## 1.5 Acceptance — P1, data only

- [ ] identity verdict recorded verbatim; if not `match`, the anchor
      result recorded instead
- [ ] resting-orders returns `ok:true, complete:true`
- [ ] each of the two outstanding orders classified: **resting /
      cancelled / filled / never reached the venue**
- [ ] each of the seven books: **settled** or **traded out** with a
      `POSITION_RESOLUTION` timestamp, or **still unresolved** + reason
- [ ] actual request count reported against the ≤8 plan
- [ ] **no attribution to "our account" absent (a) or (b)**

**Establishes nothing about profitability.**

---

# P2 — FRESH OBSERVATION

## 2.1 Why C0, stated plainly — and what this run is NOT

> *"Do not silently substitute a baseline for the intended investment
> policy."*

**There is no intended investment policy.** That is the honest position
and the package should have led with it:

| candidate | tape-backed result | status |
|---|---|---|
| C0 baseline | **negative at all four queue fractions** | baseline |
| C2 | negative at all four | **retired, not retuned** |
| C3 inventory-aware | negative at all four | failed |
| C4 incentive-aware LP | negative at all four | failed |

**Not one candidate merits capital.** So this run cannot be a strategy
evaluation — there is nothing to evaluate that we would fund.

**What it is: execution-model calibration.** The replay's fill model
rests on assumptions no snapshot corpus could test —
`queue_ahead_fraction` is swept 0.00→1.00 precisely because queue
position was never observed. C0 is used as an **instrumented probe
policy**: the widest admission (`min_spread_ticks=1`), so it generates
the most decision points per market and exercises the model across the
widest book conditions. A narrower policy would calibrate less.

| this run does | this run does not |
|---|---|
| measure data quality, gaps, clock provenance | evaluate an investment policy |
| test the frozen admission rule's inputs prospectively | establish profitability |
| compare **predicted** market-state and replay outcomes against observed, under **explicit** execution assumptions | validate fill-conditioned profitability (see §2.9) |

**Hypothesis under test (one, pre-registered):**

> Under the frozen execution assumptions, the replay's predicted fill
> counts and predicted market-state transitions on rule-admitted markets
> match observed outcomes within the stated interval.

That is a statement about the **execution model**, not about money.

## 2.2 The exact policy code

```python
epi.Policy(
    name                 = "C0",
    min_spread_ticks     = 1,
    max_mid              = 1.0,
    min_mid              = 0.0,
    placement            = "ENGINE",
    cancel_other_on_fill = False,
    max_unmatched_mult   = 1.0,
    quote_horizon_s      = 2400.0,
    recovery_wait_s      = 1200.0,
    queue_ahead_fraction = 0.00 | 0.25 | 0.50 | 1.00,   # all four reported
    execution_scenario   = "TRADE_ONLY",
    recovery             = "MAKER_THEN_TAKER",
    hard_flatten         = False,
)
size = 100 contracts per leg     # unchanged from development
```

**Selection rule** (`FROZEN_SELECTION_RULE.md`, frozen
**2026-09-22T18:05Z**), computed from the trailing 24 h *before*
subscription, inputs stored per market:

```
F1  print_interval_fraction >= 0.15
F2  median_spread_ticks     <= 2
F3  distinct_print_minutes  >= 60
```

**No parameter is tuned during or after collection.**

## 2.3 SHA, infrastructure, limits

| | |
|---|---|
| deploy SHA | pinned in `PACKAGE_A_SHA.txt` |
| lifetime repair | `5874d21`, verified an ancestor of HEAD |
| protected path | `edge-engine/src/edge/venues/pmus_stream.py` **not modified**; BETTOR reads via its own `bettor_market_stream`, separate cache |
| markets/subscription | **100** (SDK-documented ceiling), 1 subscription |
| `BETTOR_PROBE_MAX_RPS` | 0.25 · concurrency 1 |
| `BETTOR_LIVE_MAX_CONTRACTS` | **0** |
| wall clock | ≤72 h |
| **stop, authoritative** | `UPDATE ingestion_state SET value='false' WHERE key='bettor_live_observation'` — effective within `CONTROL_EVERY_S` **in the running process** |
| stop, secondary | `BETTOR_LIVE_LOOP=off` — a restart does **not** reload env on this service, only a deploy does; kept as a cheap pre-check, never the prompt control |
| stop receipt | 45 fields via `obs-stop-verdict`, incl. `shutdown_verified`, `orders_submitted` |
| rollback | revert + redeploy; observation is read-only |

## 2.4 Cadence, argued from the policy's own timescales

| measured, from 7,223 real resting orders | |
|---|---:|
| median time to fill | **86.3 s** |
| median time to cancellation | **84.9 s** |
| p90 time to fill | 365.0 s |

**At 30 s snapshots the median order lifetime contains ~3
observations.** Queue position changes on every add, cancel and
execution ahead of us; three snapshots cannot observe those events, only
bound them. **Thirty-second snapshots do not establish queue events
between snapshots** — which is exactly why `queue_ahead_fraction` is a
sweep and not a number.

**The stream is primary.** `bettor_market_stream` carries the payload's
declared fields — `marketSlug, bids, offers, state, stats,
transactTime` — i.e. **full ladders**, market **state** (a halt is not a
quiet market), and the **venue's own clock** beside our receipt clock.
Two clocks separate a slow venue from a slow consumer. HTTP BBO is a
reconciliation sample, not the record.

Every frame stores: venue `transactTime`, receipt time, `state`,
subscription epoch, reconnect counter. Every reconnect writes a gap
record with start, end and affected slugs. **A gap is a labelled hole,
never interpolated.**

## 2.5 Execution uncertainties that WILL REMAIN

1. **Our order is not in the book.** The recorded ladder never saw our
   quote. Only real resting orders close this.
2. **Intra-price queue position is unobservable** — aggregate depth per
   level is published, not per-order queues.
3. **Public prints carry no aggressor** — four columns, no side.
4. **Θ_taker = 0.0695 unvalidated** — zero executions after the
   2026-09-17 cutover.
5. **Hidden/iceberg liquidity**, if supported, is invisible.

## 2.6 Statistical protocol — reproducible

| parameter | value |
|---|---|
| primary measure | **net USD per committed capital-hour** |
| independent unit | **the event cluster** (one sporting event) |
| weighting | **equal per cluster** — capital-hour weighting let 2 of 12 markets carry 30 of 44 C4 episodes |
| significance level | **α = 0.05** |
| sidedness | **two-sided** (z = 1.960) |
| power | **0.80** (z = 0.842) |
| multiplicity | **one pre-registered primary test → no correction.** The four `queue_ahead_fraction` values are reported as a **sensitivity band, not four tests.** Secondary measures are descriptive and carry no inferential claim. |
| variance estimate | sample SD, n−1, from the 8 development clusters with episodes: **σ̂ = 0.002951** |
| minimum coverage | cluster counts only if observed admission→settlement, stream uptime ≥90%, no single gap >120 s |
| fixed endpoint | run stops on §2.7 conditions; analysis runs **once**, after |
| monitoring | data quality only. **Policy is not tuned using evaluation outcomes.** |

### σ̂ is itself uncertain — and that dominates

n = 8 gives a wide χ² interval on σ: **95% CI [0.001951, 0.006006]**.
The SD could be **2.0× larger** than the point estimate, and **n scales
with σ², so counts go up 4.1×**:

| effect δ ($/cap-hr) | n at σ_low | **n at σ̂** | n at σ_high |
|---:|---:|---:|---:|
| 0.0005 | 120 | **274** | 1,133 |
| 0.0010 | 30 | **69** | 284 |
| 0.0020 | 8 | **18** | 71 |
| 0.0030 | 4 | **8** | 32 |
| 0.0050 | 2 | **3** | 12 |

*(An earlier version quoted 239/60/15 using a population SD. The
sample SD, n−1, is the right estimator; these supersede those.)*

> **Five clusters is a COLLECTION target.** At σ̂ it detects only
> ≈0.0035 $/cap-hr — about 3× the development mean magnitude — and at
> σ_high it detects nothing useful. **No profitability conclusion may be
> drawn from the first run**, and a design sized on σ̂ alone has
> materially less power than stated if σ is at the top of the band.

### Events → calendar, and why the dates are conditional

A cluster needs a rule-admitted market observed admission→settlement.
Admission depends on **F1/F2/F3 on live markets we have not yet seen**,
so cluster yield per event day is **unknown** — the development corpus
admitted 2 of 12 at F1 ≥ 0.15, but that fraction is itself development
output and is not a forecast.

| milestone | **conditional** date | condition |
|---|---|---|
| P1 reconciliation | **2026-09-23** | one deploy, ≤8 reads |
| collection acceptance (5 clusters) | **2026-09-29 (conditional)** | ≥5 admitted markets exist across CFB Sat 09-26 / NFL Sun 09-27 **and** settle within the window |
| inference at δ=0.0020, σ̂ | **~2026-10-13 (conditional)** | 18 clusters ≈ 3 event weekends at ~6/weekend |
| inference at δ=0.0020, σ_high | **~2026-12 (conditional)** | 71 clusters |
| inference at δ=0.0010, σ̂ | **~2026-12 (conditional)** | 69 clusters |

**Both 09-29 and 10-13 are estimates conditional on eligible event
availability**, which the first run measures and which nothing currently
establishes.

## 2.7 Start / end

**Arm:** one `obs-arm` writing `bettor_live_probe_state` with a fresh
`probe_id` and explicit reservations.
**Start:** `bettor_live_observation = true`.
**End, whichever first:** (a) ≥5 admitted clusters fully observed;
(b) 72 h; (c) budget exhausted; (d) control set false.
**No look-at-results-then-extend.**

## 2.8 The 0.834 factor — development-fitted, frozen as a prediction

The decision-time model over-predicts held-out fills by ~17%;
1,099/1,317.5 = **0.834** removes it. **That factor was fitted on
2026-09-09..10.**

- Registered here as a **prospective prediction**, run forward on this
  collection with its error reported.
- **Reporting the fitted level shift as out-of-sample performance is
  forbidden** — its in-sample residual is zero by construction.
- If it does not hold prospectively, that is the finding. **It is not
  re-fitted mid-run.**

## 2.9 What this run cannot establish — and the smallest experiment that could

**Observation cannot validate our actual fill-conditioned
profitability, because we place no orders.** Predicted fills are a model
output compared against a market that never contained our quote.

**The unresolved execution measurement, exactly:**

> For a resting order of known size at a known price and known arrival
> time, the **realised time-to-fill and filled fraction**, with the
> **queue depth ahead at entry** — i.e. whether a real order at the
> touch fills when the model says it does.

**Smallest separately authorized experiment (M5) — NOT ACTIVATED, NOT
REQUESTED HERE:**

| | |
|---|---|
| shape | one resting **post-only** BUY, 4 contracts, at the touch, on one rule-admitted market |
| measures | acceptance, queue depth at entry, time-to-fill or time-to-cancel, filled fraction, maker rebate actually credited |
| gross exposure | 4 × 0.51 = **$2.04** |
| max loss | **$2.04** (post-only cannot cross; worst case the position settles at zero) |
| venue requests | ≤10 |
| runtime | ≤30 min, then cancel and stop |
| not included | no taker leg, no second lane, no trading-flag change |

**M5 is described so the gap is concrete. It is not proposed for
approval in this package**, and M4 likewise remains unexecuted.

## 2.10 Acceptance — P2, collection only

- [ ] running SHA matches the pin
- [ ] `orders_submitted = 0`; `BETTOR_LIVE_MAX_CONTRACTS = 0`
- [ ] ≥5 admitted clusters, admission→settlement
- [ ] uptime ≥90%/cluster; no gap >120 s
- [ ] every frame carries venue `transactTime` **and** receipt time
- [ ] every reconnect has a gap record
- [ ] F1/F2/F3 inputs stored per market
- [ ] stop receipt `shutdown_verified = true`
- [ ] HTTP within the armed reservation

## 2.11 Acceptance — profitability (SEPARATE; not met by the above)

- [ ] cluster count per §2.6 **at the σ actually observed**, not σ̂
- [ ] equal-weight per-cluster mean, **cluster-robust** interval
      (development design effect 2.27–2.53; assume ≥2)
- [ ] 0.834's prospective error reported
- [ ] gross and expected-to-be-paid incentive reported separately
- [ ] Θ_taker = 0.0695 validated, **or** every taker figure caveated
- [ ] **M5-class evidence** that a real resting order fills as modelled

**A green P2 is a working collector. It is not evidence of profit.**

---

# P3 — INCENTIVE TERMS

## 3.1 Retrieved — and they correct me twice

From `docs.polymarket.us/incentives/liquidity.md` (public, 0 account
requests):

```
Score = DiscountFactor ^ (ticks from best price) × OrderSize
```

- **Snapshot every second**, random within the second. Each snapshot's
  bid side and ask side are **each normalized to 1.0**, provided Target
  Size is met — so every second weighs equally regardless of liquidity.
- **Target Size** = minimum **aggregate** resting size (all
  participants) for that side to qualify. The exchange **walks from the
  best price outward** until it is met. **Orders beyond that range score
  ZERO** — proximity does not save them.
- **Periods:** Early/pre-game (listing → 6 h before), Day-of (6 h →
  start), Live (start → settlement), Daily (midnight–midnight ET).
- **Paid** within 5 business days of period end, credited within 2 more.
- **Minimum payout $1.00.** No rewards for cancelled or postponed games.
- Parameters **may change between periods**; live schedule at
  `polymarket.us/rewards`.

**Two corrections to what I previously wrote:**

1. **There is NO per-person cap.** I said there was. *"Is there a cap on
   how much one person can earn? No. Your payout is purely proportional
   to your share of the total score."*
2. **The scoring formula is not unretrieved.** It is the line above.

## 3.2 What our size would actually earn — using the real formula

At C0/C4's clip of **100 contracts at the best price** (ticks = 0, so
score = 100), share = 100 / total scored size:

| Target Size | pool $100 | pool $500 | pool $2,000 | pool $10,000 |
|---:|---:|---:|---:|---:|
| 1,000 | 10.00 | 50.00 | 200.00 | 1,000.00 |
| 5,000 | 2.00 | 10.00 | 40.00 | 200.00 |
| 20,000 | **0.50** ✗ | 2.50 | 10.00 | 50.00 |
| 50,000 | **0.20** ✗ | **1.00** | 4.00 | 20.00 |

✗ = below the $1.00 floor, so paid nothing for that unit.

**The gating risk dominates.** If resting size at prices *better* than
ours already meets Target Size, our 100 contracts score **zero**,
however close we are. At Target Size 20,000 our clip is **0.5%** of the
threshold and a single competitor resting 20,000 at the touch excludes
us entirely.

## 3.3 The payout floor, applied per unit — my previous table was wrong

> *"Concentrating $10.23 into ten or fewer units does not by itself
> ensure every unit clears $1."*

Correct. My table split a gross **evenly** across *k* units and tested
the average. Both halves were wrong. Applying the floor to each unit and
summing only those that clear, for gross $10.23 over 27 units:

| distribution | units ≥ $1 | **paid** | forfeited |
|---|---:|---:|---:|
| flat | 0 | **$0.00** | $10.23 |
| mild skew (Zipf a=0.5) | 1 | **$1.13** | $9.10 |
| moderate (a=1.0) | 2 | **$3.94** | $6.29 |
| strong (a=1.5) | 2 | **$6.21** | $4.02 |
| extreme (a=2.0) | 2 | **$7.95** | $2.28 |

**Even at extreme concentration the gross does not all arrive** — tail
units fall under the floor and are forfeited. "k ≤ 10 therefore all
pay" was an artefact of assuming an even split.

**Accounting rule:** report **gross per payout unit** and
**expected-to-be-paid** as two numbers, never one; apply $1.00 per unit;
exclude cancelled/postponed events and markets outside the programme.

## 3.4 Still missing — a separate bounded public-read allowance

The **live per-market schedule** — current reward pools, Target Sizes,
Discount Factors and eligible markets — is at `polymarket.us/rewards`,
a JavaScript application. The page fetch returned its shell; the values
come from an API the bundle calls.

**Requested separately, and NOT from the nine account requests:**

| | |
|---|---|
| allowance | **≤6 public, unauthenticated HTTP requests** |
| targets | `polymarket.us/rewards` bundle + the data endpoint it calls |
| purpose | one snapshot of pools, Target Sizes, Discount Factors, eligible markets |
| route | the existing `fetch-docs` workflow — public, no credential, no account |
| **not** | not the venue account API; **not** the 9 reconciliation requests |

**Until that lands, incentive-adjusted profitability cannot be
computed** — only the scenario grid in §3.2. The first observation run
therefore **measures qualifying exposure per payout unit** (size × ticks
from best × seconds resting, per period) and leaves the dollar
conversion open.

---

# BOUNDS AND SCHEDULE

| | |
|---|---|
| P1 venue requests | **≤8 of 9 remaining** |
| P2 venue HTTP | armed reservation; 0.25 rps, concurrency 1; 1 WS subscription ≤100 slugs |
| P3 public requests | **≤6, separate allowance** |
| wall clock | ≤72 h per observation run |
| **money at risk** | **$0.00** |
| deploys | 1 (P1+P2 share a SHA) |

| milestone | date | conditional on |
|---|---|---|
| P1 complete | 2026-09-23 | approval + deploy |
| P3 terms complete | 2026-09-23 | ≤6 public reads |
| P2 collection acceptance | **2026-09-29 (conditional)** | ≥5 rule-admitted markets exist and settle |
| profitability inference | **2026-10-13 → 2026-12 (conditional)** | 18–71 clusters depending on observed σ |

**Collection acceptance and profitability acceptance are separate lists
and the first does not imply the second.**
