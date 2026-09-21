# Closeout — bounded package, 2026-09-21

Branch `claude/session-njaewf`. Production is `3349219` and **nothing in
this package is deployed**. Standing restrictions untouched:
`mirror_live=false`, no new orders, no new capital, COMMAND undeployed,
no Phase X re-dispatch, no Track A modification. No credential was
printed, extracted, transferred or added to GitHub.

## Five separate decisions

| Decision | Verdict | Basis |
|---|---|---|
| **Implementation** (gate, harness, release controls) | **PASS** | 275 focused tests; whole 228-file surface matches the `fc80f95` baseline |
| **Production verification** (gate) | **NOT PROVEN — code-only** | the gate has never run in production; `3349219` predates it |
| **Reconciliation** | **BLOCKED, partially routed** | venue identity settled; API path built; activities read still blocked |
| **Collector feasibility** | **INFEASIBLE at current budget; recommendation contingent** | measured 1.19–1.28× oversubscribed |
| **Strategy readiness** | **NOT ASSESSED** | out of scope; no claim made |

Artifacts: `full_diff_from_3349219.patch` (8,458 lines),
`diff_stat.txt`, `focused_tests.json`, `baseline_comparison.json`,
`capacity_options.txt`.

---

## 1. The gate, without suite-wide authorization

### The fixture, and what it was concealing

`_install_snapshot_for_tests` does not merely supply a snapshot — it
**replaces the module global `_current`**. Armed suite-wide, `bind()`,
`read_state()` and the entire live read path were never executed by any
test. A broken production binding would have been indistinguishable
from a working one.

**Narrowed to an explicit allowlist of six modules** whose subject is
what the adapter puts on the wire: `test_pmus`, `test_pmus_commission`,
`test_preview_guard`, `test_s4_review_pins`, `test_side_intent`,
`test_calibration_adapter`. Every other module now runs with the gate in
its production default — unbound, denying. The non-allowlisted branch
restores *before* yielding as well as after, so a previous module's
arming cannot leak in. Exact diff and scope in `focused_tests.json`
under `gate_fixture`.

### The integration suite, fixture off

`backend/tests/test_execution_gate_integration.py`, **36 tests**, drives
`bind → authorize → read_state` with a fake pool, a real running loop,
and `submit_fok` called from a worker thread exactly as production calls
it. First test asserts `_current` has not been replaced, so if the
allowlist ever grows to include this file the whole set stops silently
pretending.

| Required | Covered |
|---|---|
| cold start / unbound denies | `test_cold_start_denies`, `..._close_too` |
| missing / malformed / unreadable / expired denies | 4 parametrised cases + `test_a_read_that_never_returns_denies` |
| authorized state permits | `test_an_authorized_bound_gate_permits_the_submission` |
| pause or halt blocks queued & retried | `test_a_pause_between_queueing_and_submitting_blocks_it`, `test_a_retry_after_a_halt_is_blocked` |
| startup binds every entry point | `test_the_binding_helper_actually_binds`, workers + API asserted |
| undeclared identity gets no extra permission | `test_an_undeclared_lane_...`, 10 parametrised near-miss lane strings |
| cancellation available | `test_cancel_works_while_paused` — exercised, not inspected |

### It found a real design flaw

`authorize()` called from the event loop's **own thread** deadlocked its
own read: `run_coroutine_threadsafe` scheduled the read on the loop the
caller was blocking, so it waited out `READ_TIMEOUT_S` and denied for the
wrong reason. Production never lands there — every order path goes
through `asyncio.to_thread` — but a future caller checking early from
async code would have. It now detects the loop thread, uses a snapshot
inside `MAX_STALE_S`, and denies immediately otherwise. Both behaviours
pinned.

The binding helper moved to `execution_gate.bind_current_loop()` so the
test that proves production binds is not skippable because
`workers/all.py` imports a notification stack needing `pywebpush`, which
is absent here.

### The decision, kept separate

**Implementation: PASS. Production verification: NOT PROVEN.** The gate
is not deployed. `3349219` has none of it. Every statement above is
about code and tests; none is evidence that the deployed system
behaves this way, and an AST census plus a green suite is not a
substitute for running it.

---

## 2. Reconciliation — identity settled, path partially routed

### Venue and account identity, settled first

Run `35619235781`. **All 52 fills are `polymarket-us`.** This mattered:
migration 008 defaults `live_orders.venue` to `polymarket-clob`, and
this session found a second submission path
(`live_executor._submit_fok`) that reaches the CLOB without touching
`pmus.submit_fok`. The reconciler could have been querying an account
that never saw these rows and reporting 52 `NOT_FOUND_AT_VENUE`.

Polymarket US and the CLOB route are kept distinct throughout. The CLOB
path is gated but carries none of these fills.

### A new limit the ledger itself imposes

Only **46 of 52 rows carry an `order_id`**. The six newest — the
`mirror` lane, **$8,911.89**, the most valuable of the set — have a
`condition_id` and no order id. Two recording regimes: older rows have
`order_id` and no `condition_id`, newer the reverse. An `order_id` join
cannot match those six whatever the venue returns; they need a
slug+time+quantity match, which is weaker evidence and must be labelled
as such.

Lanes: `mirror`/rn1 6 orders $8,911.89 · `ioc`/RN1 7 orders $3,340.23 ·
then `swisstony`, `ferrariChampions2026`, `HomeRunHazard`, `underdog`
and raw addresses.

### The authenticated runtime path

`sportsassets-api` is running, already holds working venue credentials
in its own environment, and already exposes read-only endpoints that use
them. `GET /api/desk/accounts` **already separates platform positions
from EXTERNAL ones** — a venue position on a market our ledger never
ordered — which is one of the exact questions reconciliation must
answer. `GET /api/admin/open-orders` gives resting commitments.

`venue-reconcile-via-api.yml` calls both with `X-Admin-Token`, using
`ADMIN_TOKEN`, which **already exists** as a repository secret and is
already used by `gate-lever.yml`. No venue credential moves; the API
uses its own.

**IT WORKS — run 35619426313, HTTP 200 on both endpoints.** The first
venue evidence in this effort:

```
pm.committed_usd      14500.0
pm.trading_capital    35472.89
pm.external_count     0
```

**One of these is not venue evidence, and I nearly used it as such.**
`committed_usd` is `settings().committed_capital_pm_usd` — an
owner-set configuration constant. Compared against the ledger's
$16,180.53 it would have manufactured a $1,680.53 "discrepancy" out of
a config value. It is now labelled at the point of printing.

What *is* venue-derived: `external_count`, computed from the venue
portfolio's `open_positions` against our own order ledger. **Zero
externals** — no venue position on a market we never ordered.

**And zero externals is ambiguous**, which I am not going to paper
over: either every venue position matches our ledger, or the venue
holds no open positions at all. The second would mean $16,180.53 is
entirely stale. A follow-up run printing the payload shape and every
list length was dispatched (35620634497) and was still queued behind
saturated runners at the time of writing. **Unresolved.**

**Scope:** these endpoints return *current* state. No trade-level
activity, no settlement events, so this lane **cannot** match the 52
historical fills row by row or establish settled proceeds.

### Remaining blocker, precisely

Two, now distinguished:

1. **Row-level fill match and settled proceeds** — needs
   `portfolio.activities` (TRADE + POSITION_RESOLUTION). No existing
   endpoint exposes it. Either repository secrets `PMUS_KEY_ID` /
   `PMUS_SECRET_KEY` (read-only venue scope) for `venue-reconcile.yml`,
   **or** a new read-only endpoint on the API — which is a deployment,
   outside existing authorization.
2. **The six order-id-less rows** — unmatched by any join even with
   full venue access.

Exact invocation if the secrets are granted:
`Actions → venue-reconcile → Run workflow (since_days=75)`.

**Reconciliation remains ABSENT, not zero.** No accounting record has
been altered and internal-ledger agreement has not been substituted for
venue confirmation. The four figures stay separated: historical
acquisition cost ($16,180.53, a sum over fill records); current
holdings; open-order commitments; settled cash — the last three
unmeasured.

---

## 3. Collector capacity — definitions, qualifications, options

### The window and the definitions

From `bettor_capacity_inputs.sql`, 12-hour window, V4 rows
(568 ticks, span ≈ 39,150 s ≈ 10.9 h):

```
created/min = sum(obs_written) * 60 * |horizons| / span_seconds
            = 507 * 60 * 4 / 39150  = 3.106 tasks/min
served/min  = sum(fu_attempted) * 60 / span_seconds
            = 1590 * 60 / 39150     = 2.435 reads/min
```

### Observed throughput is not demonstrated capacity

**2.435/min is what the system did, not what it could do.** The system
was never run with a full queue at full budget. Pacing averaged 2.054,
which cuts the tick budget to `int(10/2.054) = 4` and the reserve to 2;
observed use was 2.80 reads/tick, a mix across pacing regimes. At
sustained pacing 1.0 the reserve would be 5, giving ≈ 4.34 reads/min —
so observed service is roughly **56% of the full-budget ceiling**, and
that ceiling is itself an arithmetic bound, not a measured maximum.

The backlog stood at 11.8–17.0 with drain times 317–487 s, so work was
available: the system was budget-limited rather than work-limited.

**Measured directly (run 35619569497): `pct_of_reserve_used = 94.4%`** —
2.83 reads used against 3.00 available per tick. So observed throughput
*is* essentially the capacity available **under the prevailing pacing**,
and pacing is adaptive and set by the venue's 429s. The remaining
headroom is not in the scheduler; it is in what the gateway will serve.

The rate window is rolling. The later run gives V4 = 444 ticks,
03:39:29Z–12:08:24Z, span 30,535 s, 395 obs_written, 1,196 fu_attempted
→ created 3.104/min, served 2.350/min. The earlier figures (3.106 /
2.435) came from a window shifted eight hours later; both are
internally consistent and each is cited with its run.

### The 86.9% is an assumption-based upper bound, not a measured ceiling

I called 60 / 69.03 a scheduling ceiling. It is not automatically one.
It holds under five assumptions I neither stated nor tested:

| | Assumption | Status |
|---|---|---|
| A1 | service is instantaneous at a tick instant | idealisation |
| A2 | ticks are exactly periodic at the mean gap | **FALSE** — min 34.21 s, max 92.92 s |
| A3 | due moments are uniform relative to tick phase | untested |
| A4 | one tick inside the window suffices | holds only if capacity is free |
| A5 | within-tick processing costs nothing | **FALSE** — reads are serial and paced |

The correct bound is `E[min(G, 2·tolerance)] / E[G]` over the actual
gaps, not `2·tolerance / E[G]` over their mean.

**Measured (run 35619569497), and the correction is small:**

| | value |
|---|---|
| bound from the gap distribution | **87.05%** |
| bound from the mean alone | 87.50% |
| **overstatement** | **0.45 pp** |

Gaps are far more regular than min/max suggested: 629 gaps, mean
68.57 s, **SD 5.62 s**, p10 64.41, p90 73.40, and only **5 of 629 below
60 s**. So A2 is approximately true in practice and my arithmetic error
was under half a percentage point.

A5 is real but modest: 2.83 reads per tick at 2.027 s pacing is
**5.75 s of serial pacing inside a tick**, about a tenth of the 60 s
window.

**Label: ~87% is an assumption-based upper bound on on-time service at
this tick cadence, now computed from the gap distribution rather than
its mean.** It is a property of this cadence, not a universal
scheduling ceiling. The number was nearly right; presenting it as a
measured limit was not.

### Both V1 failures preserved, separately

They are two defects and a candidate must survive each alone.
Collapsing them would let a policy that fixed only the brake look
complete.

- `test_v1_failure_one_integer_division_reduces_admission_to_zero` —
  demonstrated with an **empty** queue at reserves 1, 2, 3, so the brake
  cannot be the cause.
- `test_v1_failure_two_a_level_is_compared_with_a_per_tick_flow` —
  demonstrated at reserve 8, where `8 // 4 == 2`, so the divisor cannot
  be the cause.
- `test_the_candidate_survives_both_failures_independently` at every
  reserve.

### The four options, measured

Same workload (0.777 obs/min, 69.03 s tick, budget 6, inherited queue,
240 min). **Absolute rates are optimistic** — the harness aligns
arrivals to tick boundaries, so treat these as a *relative* ranking:

| Option | oversub | obs admitted | on-time | expired |
|---|---|---|---|---|
| baseline | 1.19× | 185 | 73.3% | 133 |
| **read_more** (budget 12) | 0.60× | 185 | **89.2%** | 2 |
| **fewer_horizons** (drop 3600 s) | 0.89× | 185 | **93.8%** | 18 |
| shorter_tick (50 s) | 0.86× | 186 | 77.7% | 16 |
| admit_less (0.65/min) | 1.00× | **155** | 77.1% | 16 |

### Recommendation

**`read_more` — raise the follow-up reserve — contingent on a venue
capacity probe that has not been run.**

Reasoning against the evidence requirements: BETTOR needs
`E[SETTLEMENT − QUOTE | STATE]` at the declared horizons, and
statistical power scales with coverage. `read_more` is the only option
that costs **neither coverage nor a horizon**, and it gives the largest
feasibility margin. The pacing backoff already makes the collector yield
to the venue, so a higher reserve raises its ceiling without removing
the protection the money path relies on.

**The contingency is not a formality.** The harness models the budget as
*available*; it does not model whether the venue would serve 12 reads a
tick. Production logged 88 rate-limited and 48 unreadable reads over 568
ticks at a budget of 4–10. Until a probe measures what the gateway will
actually serve, `read_more`'s benefit is assumed, not demonstrated.

**Named fallback if the venue will not serve it: `fewer_horizons`.** It
scores best on timing at no coverage cost — but permanently loses the
3600 s horizon, which cannot be reconstructed later. That is a research-
design decision, not an engineering one, and it stays with you.

`admit_less` is the worst trade: 16% of coverage for 77.1%.

**V2 measure-only remains the production baseline. No candidate is
deployed. Horizons, tolerances and denominators are unchanged** — the
on-time rate is taken over every task created, and declined observations
are reported beside it, never inside it.

---

## 4. Evidence for the grouped-test hang — CONFIRMED

Claim: the hang predates this branch.

Files 71–90 of the regression surface hang when run *together*, though
every file passes alone. Run on the **`fc80f95` worktree** — an
untouched tree — with the 19 files both trees share, the run was
terminated at the 200 s bound exactly as on HEAD.

```
cd /tmp/claude-0/base-fc80f95/backend && \
  timeout -k 5 200 python -m pytest -q -p no:randomly <files 71-90>
→ Terminated (rc=143)
```

**Predates this branch: confirmed. Root cause: unconfirmed** — the
behaviour is consistent with leaked module-level database state, but I
have not isolated it and do not claim it.

---

## What no claim here rests on

- No deployment or capital-readiness claim rests on a permissive test
  fixture: the fixture is now an opt-in allowlist of six modules, and
  the gate's initialization is proven with it off.
- No claim rests on an unreconciled internal ledger: reconciliation is
  reported absent, and $16,180.53 is labelled a sum of acquisition costs
  throughout.
- No assumption is presented as a measured limit: the timing bound is
  now computed from the gap distribution (87.05%) rather than from its
  mean (87.50%), and labelled a property of this tick cadence rather
  than a universal ceiling.
- No configuration constant is reported as venue evidence:
  `committed_usd` is labelled as `settings().committed_capital_pm_usd`
  at the point of printing, and is not compared with the ledger.
