# FINAL HANDOFF — reconciliation and collector capacity

Production remains **3349219**. Nothing in this package is deployed.
`mirror_live=false`, COMMAND undeployed, no Phase X re-dispatch, no Track A
modification, no order submitted, no capital authorized, no credential moved.
Every read below was read-only. **No accounting record was altered.**

Status by the five categories asked for:

| | |
|---|---|
| Execution gate | **implementation verified; production verification pending** |
| Account reconciliation | **specifically blocked** — three named blockers, §1.7 |
| Collector recommendation | **conditional** — one measured refutation, one unverified rate increase, §2.7 |
| Strategy readiness | **not assessed** (not reopened) |

---

## 1. ACCOUNT RECONCILIATION

### 1.1 What the venue reported

`/api/desk/accounts` through the API service's own credentials, run
[35621687030](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35621687030),
`as_of` 1790005856.261774, `role` admin, HTTP 200.

```
pm.configured        True          pm.positions           list of 0
pm.error             None          pm.external_positions  list of 0
pm.cash              20972.89      pm.open_value          0.0
pm.account_value     20972.89      pm.unsettled_funds     0.0
pm.buying_power      20972.89      pm.realized_pnl        2058.85
pm.recent_trades     list of 25    pm.committed_usd       14500.0   <- CONFIG CONSTANT
```

`configured` true, no `error` key, `positions` present and empty. That is a
**successful read returning no holdings** — distinct from a failed read, an
absent key, or a null. The conclusion is bounded to what it supports: *no
holdings were reported for that account in that snapshot.* It does not
establish realized P&L, recovered principal, or the disposition of the
historical acquisition cost.

`committed_usd` is `settings().committed_capital_pm_usd`, an owner-set
constant. It is **not** a venue reading and must not be differenced against
the ledger; doing so would have manufactured a $1,680.53 discrepancy out of a
config value. `totals.*` ($66,328.33 cash, $80,828.33 value) spans Kalshi and
Polymarket together and is not the PMUS figure.

### 1.2 The three reported categories, kept separate

| | |
|---|---|
| **Platform positions** | 0 rows reported |
| **External positions** | 0 rows reported (`external_count` 0, list present and empty) |
| **Outstanding orders** | **NOT ESTABLISHED.** See 1.3. |

### 1.3 Correction: `/api/admin/open-orders` is not a venue read

I previously presented that endpoint as outstanding venue commitments. It is
not. It reads `live_orders WHERE whale_username='manual' AND status='open'` —
a **database read of the manual lane**. No venue read of resting orders exists
on any path available here, so **outstanding venue order commitments remain
unestablished** in both directions.

### 1.4 Blocker: account identity cannot be verified

`account_snapshot()` returns `{configured, error?, balances, positions,
recent_trades}` and names **no wallet, address, proxy, account id, owner or
user**. The reader now enumerates identity-bearing keys rather than assuming;
it printed `NONE`.

So: `live_orders.venue = 'polymarket-us'` on all 52 rows establishes **the
ledger's own label**. It does not establish that the authenticated API account
is the account that generated those fills. That link is unverified and cannot
be verified through this path. **Named missing capability: an account-identity
field (wallet / proxy address / account id) on the PMUS account snapshot, or a
venue endpoint that echoes the authenticated principal.**

### 1.5 The 52 fills are a residual, not a portfolio

This is the finding that reframes the whole question. `live_orders` is not 52
open positions beside a settled history. It is 166,585 rows in eight states:

| status | rows | settled_at | pnl | filled_usd | pnl_sum |
|---|---|---|---|---|---|
| settled | 5,271 | 5,271 | 5,271 | 425,812.30 | −31,124.16 |
| cashed_out | 457 | 457 | 454 | 40,113.00 | +3,607.75 |
| **filled** | **52** | **0** | **11** | **16,180.53** | **−3,775.65** |
| rejected | 152,444 | 0 | 0 | 0 | — |
| unfilled | 7,202 | 0 | 0 | 0 | — |
| merged | 581 | 0 | 0 | 0 | — |
| cancelled | 382 | 0 | 0 | 0 | — |
| error | 196 | 0 | 0 | 0 | — |

Every settled and cashed-out row carries `settled_at`. **None of the 52 does.**
They are the rows whose disposition was never written back — consistent with
the venue holding nothing, but that consistency is not itself proof.

### 1.6 The four categories, kept apart

| category | amount | basis |
|---|---|---|
| **Historical acquisition cost** | **$16,180.53** | ledger `filled_usd`, 52 rows, 2026-08-04 → 2026-09-10. Internally inconsistent — see 1.8. |
| **Verified current holdings** | **$0.00 reported** | one successful venue snapshot, account identity unverified |
| **Verified outstanding order commitments** | **NOT ESTABLISHED** | no venue read of resting orders exists |
| **Verified settlement / sale proceeds and fees** | **NOT ESTABLISHED** | see below |
| **Unexplained difference** | **$15,036.19** | 41 of the 52 have no closure recorded on their slug under any status |

On the last line: 11 of the 52 sit on a slug that *does* carry a `settled` or
`cashed_out` row elsewhere ($1,144.34 of acquisition) — those are plausibly
bookkeeping residue from a position that did close. The other 41
(**$15,036.19**) have no recorded closure anywhere in the ledger.

No proceeds figure is available at all. `realized_pnl: 2058.85` is a venue
field but it is an aggregate with no window, no scope and no reconciliation to
our rows. **Fees were not reported by any endpoint reached.**

### 1.7 Three specific blockers

1. **Account identity** — no identifier on the snapshot (1.4).
2. **Trade history depth** — `recent_trades` returns **25 rows spanning
   2026-09-20T21:42:55Z → 2026-09-21T00:45:06Z**. The 52 fills run
   2026-08-04 → 2026-09-10. **Zero of 52 fall inside that window.** The window
   cannot corroborate a single historical fill; it is the wrong instrument, not
   a failed match. Needed: a paginated fills/trades endpoint covering
   2026-08-04 onward.
3. **Resting orders** — no venue read exists (1.3).

None of these is a GitHub-secrets problem. The API service holds working
credentials and answered; these are **capability gaps in the endpoints**, and
they are the reason reconciliation is *blocked* rather than *complete*.

### 1.8 Two things I had wrong, and a ledger-wide defect

**(a) The six rows without `order_id`.** All six are the mirror lane
(`lane='mirror'`, `lane_user='rn1'`), all `polymarket-us`, all BUY, all with
`condition_id` + `asset`. An `order_id` join cannot reach them. Attempting
reconstruction from their own fields:

| id | slug | filled_shares | orig_shares | price | recorded_usd | filled×price | orig×price | requested_usd |
|---|---|---|---|---|---|---|---|---|
| 412606 | astatc-cs2-mibr-bb-…map2 | 0.45 | 11819 | 0.435716 | 6669.97 | 0.20 | 5149.73 | **6746.89** |
| 412103 | aec-wta-ireesc-mirbul-… | 0.24 | 6021.29 | 0.862240 | 1140.38 | 0.21 | 5191.80 | **1140.39** |
| 411867 | aec-wta-cadbra-sartor-… | 32.00 | 1519 | 0.640000 | 972.16 | 20.48 | **972.16** | **972.16** |
| 412118 | aec-itfme-trimcc-xavpal-… | 0.05 | 244 | 0.629795 | 90.33 | 0.03 | 153.67 | 91.74 |
| 412244 | atc-spl-fat-dir-…dir | 0.43 | 34 | 0.715588 | 24.33 | 0.31 | **24.33** | 24.57 |
| 412451 | aec-itfwo-yuhwan-yufren-… | 0.00 | 137 | 0.130000 | 14.72 | 0.00 | 17.81 | **14.72** |

`filled_usd` tracks **`requested_usd`**, matching to the cent on three rows and
within 1.5% on two more. It does not track either share count times price.
**So the $8,911.89 attributed to these six is a REQUEST figure, not a confirmed
executed amount.** Labelled per instruction: all six **unmatched** — no venue
row is available in the retrievable window, and none was invented.

**(b) A ledger-wide arithmetic inconsistency, not a property of the 52.** I was
about to present this as specific to the unclosed rows. The control says
otherwise:

| status | rows | reconcile `filled_shares × fill_price` | pct |
|---|---|---|---|
| filled | 52 | 37 | 71.15% |
| settled | 5,267 | 3,711 | 70.46% |
| cashed_out | 457 | 79 | 17.29% |

≈30% of the *settled history* fails the same check. So `filled_usd` and
`filled_shares × fill_price` disagree as a standing property of how
`live_orders` records fills — it is not evidence about the 52. Across the 52:
37 reconcile on `filled×price`, 40 on `orig×price`, **11 on neither**, 1 has
`orig_shares` NULL.

### 1.9 An unexplained venue/ledger divergence — flagged, not resolved

The ledger has **no row of any status after 2026-09-10** (last day: 295 rows).
`live_orders` in the last 7 days: **0**. Yet the venue reported 25 trades on
**2026-09-20 and 2026-09-21** — NFL markets (`aec-nfl-ind-kc-2026-09-20`,
`aec-nfl-was-dal-2026-09-20`, `asc-nfl-lv-lac-…`, `tsc-nfl-jax-den-…`,
`caoc-9c8f7da6507e9416`) that this ledger has **never seen in any status** (0
rows matched). Several are BUYs of 800–1,300 shares and SELLs of up to 10,142
shares.

Two readings, and I cannot choose between them with the evidence available:
either **the authenticated account is not the account that generated our
fills**, or **our account was traded on 2026-09-20/21 by something outside this
system**. Resolving it requires 1.7 blocker 1. Three of the seven slugs were
printed at exactly 34 characters and may be truncated; the four that are not
also matched nothing, and no ledger row of any date could match a 2026-09-20
slug.

Not touched, per instruction: no record was adjusted, no row closed, no
reconciling entry written.

### 1.10 mirror_books, both directions

| state | books | venue never read | ledger_net vs venue_net disagree | ledger_net | venue_net |
|---|---|---|---|---|---|
| closed | 1,624 | 848 | 122 | −41,550 | −784 |
| closing | 6 | 6 | 0 | +33 | — |
| frozen | 1 | 0 | 1 | −1,444 | 0 |

1,185 slugs in `mirror_books` have no filled `live_order`; 41 slugs have a
filled `live_order` and no book. The frozen book was touched at
**2026-09-21 15:55:42**, twelve seconds before the query ran — the mirror
worker is live and writing books while submitting no orders, which is what
`mirror_live=false` should look like.

---

## 2. COLLECTOR CAPACITY

### 2.1 The window, and whether it is clean

6 hours to 2026-09-21T15:58Z, 319 ticks, **359.08 min span**. It is **not
single-era** and pooling is stated rather than hidden:

| admission_version | ticks | span |
|---|---|---|
| `BETTOR_ADMISSION_V2_MEASURE_ONLY` | 193 | 12:18:58 → 15:57:57 |
| *(none recorded — pre-admission V4)* | 114 | 09:58:53 → 12:08:24 |
| `BETTOR_ADMISSION_V1_CAPACITY_AWARE` | 12 | 12:07:48 → 12:20:02 |

V2 is **measure-only**, so it does not throttle and behaves identically to
pre-admission for intake purposes; the 12 V1 ticks are 3.8% of the window.
`pacing_version` is `BETTOR_CAPTURE_PACING_V4_ALLOWANCE_RESTORED` throughout.

### 2.2 Arrivals, allocated capacity, achieved service — one window, one unit

All three in **follow-up reads per minute**.

| | reads/min |
|---|---|
| **Arrivals** (observations written × 4 horizons) | **3.286** |
| **Allocated capacity** (`fu_reserve`, replayed from recorded pacing) | **2.554 – 2.593** |
| **Achieved service** (`fu_attempted`) | **2.565** |

Allocated capacity is **not a constant I chose**. It is the collector's own
formula replayed against each tick's recorded pacing —
`total_budget = max(1, int(10 × min(1, 1/pacing)))`,
`fu_reserve = max(1, total_budget // 2)` — verified against the Python at
twelve pacings. The `total_budget = 1` branch alternates 0/1 on `_TICK_SEQ`
parity, which telemetry does not record, hence the range (14 such ticks).
Window means: total budget 6.11 reads, reserve 3.01 reads, pacing 2.022 s.

Reading the three:

- **Oversubscription = 3.286 / 2.593 = 1.267×.** The queue grows without bound;
  no ordering rule fixes that.
- **Utilisation = 2.565 / 2.593 = 98.9%** of the high estimate. The reserve is
  essentially fully consumed. *(An earlier, different window gave 94.4%. Both
  say "nearly consumed"; this window's figure is the one that belongs with
  these arrivals.)*
- **Deficit = 0.721 reads/min = 21.9% of created work never serviced**
  — about 259 tasks over the window.

### 2.3 Deadline feasibility, all four horizons preserved

| horizon | reads | on_time | late_recovery | not_observable | pct on-time | p50 lag | p50 ÷ horizon |
|---|---|---|---|---|---|---|---|
| **60 s** | 232 | 84 | 148 | 0 | 36.21% | 272.0 s | **4.53×** |
| **300 s** | 228 | 56 | 172 | 0 | 24.56% | 636.8 s | 2.12× |
| **900 s** | 234 | 72 | 162 | 0 | 30.77% | 1176.1 s | 1.31× |
| **3600 s** | 225 | 58 | 167 | 0 | 25.78% | 3970.8 s | **1.10×** |

Two things fall out that were not visible before.

**Round-robin fairness is fine.** Reads are near-uniform across horizons
(232/228/234/225). No horizon is starved. The earlier worry about starved
horizons does not hold in this window — the problem is timeliness, not
fairness.

**The 60 s horizon is defeated by cadence, not by capacity.** Tolerance is
±30 s, so an on-time 60 s read must land in [30, 90] s — a 60-second target
window. The tick period is ≈71 s. A scheduler that only gets a chance every
71 s cannot reliably hit a 60-second window, and no number of extra reads
changes that. This is exactly the distinction between relieving backlog and
fixing deadline misses caused by scheduling cadence. The 3600 s horizon, by
contrast, misses by only 10% at the median and is nearly feasible.

### 2.4 The denominator, stated separately

919 `bettor_state_mids` rows in the window; **919 parsable lags, 0 null, 0
unparsable** — nothing was silently dropped from the numerator.

But the percentages in 2.3 are computed over **reads that happened**. Tasks
that expired unread never produce a row:

- work created: 3.286/min × 359.08 min = **1,180 tasks**
- reads that happened: **919**
- on-time reads: 84+56+72+58 = **270**

**On-time against reads taken: 29.4%. On-time against work created: 22.9%.**
Both are reported; the second is the honest one.

### 2.5 The bounded read-only probe, and what it found

Asked for: a bounded read-only probe supporting `read_more` through an already
authorized path, within documented rate limits, without starving the collector.

**A burst probe has no non-contending path.** The Actions concurrency group
`pmus-public-read-global` governs *workflows*; the collector runs continuously
on Render, **outside** it. A probe would contend with a collector whose pacing
is adaptive — degrading the thing the measurement is for, and corrupting its
own result. **That is the named blocker.**

So the probe was run against telemetry the collector has already recorded, at
**zero additional venue load** — it backs off on 429 and recovers on success,
so it has already visited a range of request rates and logged the refusals.
*Latency, errors/throttling and successful reads come from those records.*

**Refusals by reads issued in a single tick**, 6-hour window / 7-day window:

| reads in tick | ticks (6h) | pct 429 (6h) | ticks (7d) | pct 429 (7d) |
|---|---|---|---|---|
| 1 | 17 | 0.00% | 39 | 0.00% |
| 2 | 49 | 1.02% | 139 | 0.36% |
| 3 | 70 | 2.38% | 205 | 2.93% |
| 4 | 119 | 2.73% | 372 | 3.02% |
| 5 | 17 | 2.35% | 73 | 4.38% |
| 6 | 13 | 0.00% | 50 | 7.00% |
| **7** | 22 | **25.97%** | 59 | **23.73%** |
| 8 | 8 | 0.00% | 20 | 4.38% |
| 9 | 4 | 0.00% | 15 | 0.00% |

**Refusals are a standing limit, not an incident.** Over 7 days they occur in
**every one of 19 hours** (2–21 per hour on 133–248 reads). By my own stated
criterion — a cluster is a venue incident, a spread is a standing limit — this
is a standing limit. The collector already operates at the refusal boundary.

The **7-read spike reproduces in both windows at ~24–26%** while 8 and 9 sit at
0–4%. I cannot explain the non-monotonicity and will not paper over it; the 8-
and 9-read buckets have 8 and 4 ticks (6h) / 20 and 15 (7d), far too few to
call safe. What the data does support: **at ≤6 reads per tick refusals stay
≤3%; above that they are erratic and once 26%.**

**And `read_more` is refuted on its own terms.** Per total budget:

| total_budget | ticks | fu_reserve | avg fu served | avg reads issued | rate limited |
|---|---|---|---|---|---|
| 5 | 50 | 2 | 3.16 | 4.10 | 11 |
| 6 | 36 | 3 | 3.53 | 4.28 | 1 |
| 8 | 28 | 4 | 3.68 | 4.79 | 0 |
| **10** | **83** | **5** | **3.16** | **4.63** | **38** |

At the **largest** budget the collector serves **3.16 against a reserve of 5**
and issues 4.63 reads against a budget of 10 — and collects **38 of the
window's 51 refusals**. It already cannot spend the reserve it has. Raising the
reserve adds allocation that is not consumed, at the burst sizes that get
refused. *There is no observed operating point above today's that the gateway
served cleanly with a meaningful sample.*

Section 5 of the 7-day query: `live_orders` in the last 7 days = **0**. The
mirror lane is not currently competing for this gateway — but that is a
statement about a quiet week under `mirror_live=false`, not a reservation. Any
headroom found is shared.

### 2.6 The one recommended configuration

**All four horizons preserved.** Two changes attacking two different failures,
because neither alone is sufficient and `read_more` is refuted.

| # | change | from | to | why |
|---|---|---|---|---|
| 1 | `TICK_PERIOD_S` | ≈71 | **45** | Cadence. With 2×tolerance = 60 s > 45 s + jitter, the timing ceiling becomes 1.0 for all four horizons instead of 60/71 = 84.5%. This is the only lever that touches the 60 s horizon. |
| 2 | `MAX_READS_PER_TICK` | 10 | **6** | Burst cap. Removes the ≥7 regime carrying 40 of 51 refusals at ~26%, at no cost — the collector issues only 4.63 reads at budget 10 anyway. |
| 3 | `fu_reserve = total_budget // 2` | — | unchanged | At budget 4–6 the reserve is 2–3. |
| 4 | admission | V2 measure-only | **V3 enforcing** | `admit_cap` such that admitted_obs/min × 4 ≤ fu_reserve × 60/T. **Float arithmetic, not integer division**, with `ADMIT_FLOOR_OBS = 1`. |

**Intake and service rates at the recommendation** (T = 45 s, R = 4, F = 2):

- service = 2 × (60/45) = **2.667 follow-up reads/min**
- sustainable intake = 2.667 / 4 = **0.667 observations/min**
- current intake = 3.286 / 4 = 0.822 obs/min → **a 19% reduction in
  observations admitted**, in exchange for every admitted observation getting
  all four of its reads
- arrivals 2.667 = service 2.667 → **oversubscription 1.00×**, queue stable

**Timing for all four horizons:** ceiling rises from 84.5% to 1.0 for every
horizon because the tick gap no longer exceeds the tolerance window. The 60 s
horizon stops being structurally infeasible. Achieved on-time will sit below
the ceiling by whatever read latency and queueing cost — the 3600 s horizon's
p50 overshoot of 371 s is queueing, not cadence, and may persist.

**Overload and recovery:** keep the existing adaptive backoff untouched. The
admission cap recomputes **each tick from that tick's current budget**, so a
backed-off tick automatically admits less; `ADMIT_FLOOR_OBS = 1` prevents the
latch-to-zero that broke V1 (`fu_reserve // horizons_per_obs == 2 // 4 == 0`),
and that exact production failure stays in the regression set.

**Resource and API demand:** burst size falls 10 → 6 (cap) with the typical
tick unchanged at ~4. Sustained request rate rises from 4.63 reads / 71 s =
0.065/s to 4 reads / 45 s = **0.089/s, +36%**. Database write volume falls with
intake. No new credential, endpoint or permission.

### 2.7 Why this is conditional, and what would settle it

1. **The +36% sustained request rate is unverified.** Burst size stays at 4,
   which the gateway served at 2.4–3.0% refusals — but rate and burst are
   different axes and I have not measured the gateway at this rate. **The
   probe that would measure it has no non-contending path (2.5).**
2. **The 7-read refusal spike is unexplained** and reproduces in two windows.
3. **The 3600 s horizon's 10% median overshoot** is queueing delay; cadence
   will not fix it.

The cheap way to settle (1) without a probe: ship with V2's measure-only
telemetry still recording, watch `obs_rate_limited` for one hour against the
baseline in 2.5, and roll back on a rise. That is a deployment decision, not
one I have taken.

**`fewer_horizons` is not recommended and was not used as a fallback.** It
would balance the book on its own (4→3 horizons cuts arrivals 25% to 2.465,
just under achieved service) but that is an **evidence tradeoff** — permanently
losing one horizon of the corpus — and it requires a separate scope decision.
It is recorded here as an available option, not taken.

---

## 3. THE PACKAGE

Branch `claude/session-njaewf`, final SHA **`a2fdc8e`** (32 commits from
production `3349219`).

| | |
|---|---|
| Full diff | [`research/beta48/acceptance/full_diff_from_3349219.patch`](https://github.com/matthewtaylor141-lab/SportsAssets/blob/claude/session-njaewf/research/beta48/acceptance/full_diff_from_3349219.patch) (8,458 lines) |
| Diff stat | [`diff_stat.txt`](https://github.com/matthewtaylor141-lab/SportsAssets/blob/claude/session-njaewf/research/beta48/acceptance/diff_stat.txt) |
| Test results | [`focused_tests.json`](https://github.com/matthewtaylor141-lab/SportsAssets/blob/claude/session-njaewf/research/beta48/acceptance/focused_tests.json) — 275 passed |
| Baseline comparison | [`baseline_comparison.json`](https://github.com/matthewtaylor141-lab/SportsAssets/blob/claude/session-njaewf/research/beta48/acceptance/baseline_comparison.json) |
| Earlier closeout | [`CLOSEOUT.md`](https://github.com/matthewtaylor141-lab/SportsAssets/blob/claude/session-njaewf/research/beta48/acceptance/CLOSEOUT.md) |
| The gate | [`backend/sportsassets/execution_gate.py`](https://github.com/matthewtaylor141-lab/SportsAssets/blob/claude/session-njaewf/backend/sportsassets/execution_gate.py) |
| Gate integration tests | [`backend/tests/test_execution_gate_integration.py`](https://github.com/matthewtaylor141-lab/SportsAssets/blob/claude/session-njaewf/backend/tests/test_execution_gate_integration.py) — 36 tests, fixture OFF |
| Admission model | [`backend/sportsassets/bettor_admission.py`](https://github.com/matthewtaylor141-lab/SportsAssets/blob/claude/session-njaewf/backend/sportsassets/bettor_admission.py) |

Evidence runs (all read-only, all successful):

| run | what |
|---|---|
| [35621687030](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35621687030) | venue account via the API's own credentials |
| [35622076376](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35622076376) | gateway capacity from collector telemetry, 7 days |
| [35622240478](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35622240478) | the 52 fills against the venue window |
| [35622551814](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35622551814) | capacity balance sheet, one window one unit |
| [35622838582](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35622838582) | where the 52 went |

Runs 35620634497 and 35621014265 were retrieved as instructed; both completed
successfully and are superseded by 35621687030, which reads the same endpoints
with the corrected parser. No duplicate job was dispatched while an equivalent
read was queued.

No test sweep was repeated. The only checks run were the reads above and a
twelve-point verification of the budget-formula replay against the Python.

### 3.1 Two defects I introduced and fixed, recorded

**The runner starvation was mine.** I added an unbounded
`bash <(curl ...)` actionlint step to `commit-guard.yml`; the process
substitution never returned, twenty runs sat `in_progress` for over an hour
each holding a runner, and the account's queue starved. I had reported the
resulting delays as externally "saturated runners." The step is now bounded
(`timeout-minutes: 3`, `curl --max-time 60 -o file </dev/null`,
`timeout 90 bash … </dev/null`, download failure downgraded to a warning), the
job carries `timeout-minutes: 10`, and 18 stuck runs were cancelled.

**A cosmetic psql defect**, recorded so it does not recur: `\echo` treats an
apostrophe as an unterminated quoted string. `VENUE'S` in a heading printed an
error mid-file. Results were unaffected; later files avoid apostrophes.
