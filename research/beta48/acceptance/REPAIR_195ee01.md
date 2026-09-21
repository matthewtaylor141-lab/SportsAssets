# REPAIRS TO 195ee01 — four defects an independent review reproduced

Production remains **3349219**. This candidate is **not deployed**, no flag
was changed, no trading enabled, and **no accounting record altered**.
`mirror_live=false`, COMMAND undeployed, no Phase X re-dispatch, no Track A
modification.

All four findings were correct. All four were mine.

| | status |
|---|---|
| 1. Execution gate fail-open paths | **fixed**, reproductions now fail safely |
| 2. Admission accounting | **fixed**, V3 retained as a regression fixture |
| 3. Capacity evidence | **corrected** — the repair makes the numbers worse, not better |
| 4. Reconciliation precision + capability prep | **corrected and built** — two of my three "blockers" were my own wiring |

**The unqualified execution-gate implementation PASS is withdrawn**, as
instructed, and replaced by: *implementation verified against the specific
reproductions below; production verification still pending; the gate remains
undeployed.*

---

## 1. EXECUTION GATE — both fail-open paths closed

### 1.1 What was wrong

`_current()` had two branches that returned a cached snapshot instead of a
fresh read, and `authorize()` accepted whatever it returned:

```python
except Exception:                       # the live read failed
    if snap.ok and age <= MAX_STALE_S:  #  <-- a five-second-old YES
        return snap
...
if on_loop_thread:
    if snap.ok and age <= MAX_STALE_S:  #  <-- and again, unconditionally
        return snap
```

The review's reproduction — install a recent allowed snapshot, make
`run_coroutine_threadsafe` raise `TimeoutError`, call
`authorize("submit", lane="manual")` — **returned successfully**. The module
docstring said unreadable authorization denies. The code said a five-second-old
yes was good enough. A kill switch that keeps saying yes for five seconds after
it stops being readable is not a kill switch.

Separately, `_parse_switch()` ended `return bool(parsed)`. Python truthiness is
not JSON's boolean, so every one of these decoded to falsy and therefore to
**NOT PAUSED**:

| stored value | decodes to | old verdict |
|---|---|---|
| `"0"` | `0` | trading allowed |
| `"1"` | `1` | trading allowed |
| `"[]"` | `[]` | trading allowed |
| `"{}"` | `{}` | trading allowed |
| `"null"` | `None` | trading allowed |
| `'""'` | `""` | trading allowed |
| `'"false"'` | `"false"` | trading allowed |

### 1.2 The repair

**Cached state and authorization are now different functions and are not
interchangeable.**

| | |
|---|---|
| `_authorize_read()` | fresh read, or a denying snapshot. **No branch returns the cache.** The only input to `authorize()`. |
| `_last_known()` | whatever was last read, with its age. Feeds `describe()` and nothing else. |

`describe()` keeps the cache — diagnostics may be stale — and now carries
`stale` and a note saying the view never authorizes anything.

The loop-thread caller no longer gets a cached yes. A synchronous submission
that cannot obtain a fresh read is **denied**, and the denial names the
supported path: **`authorize_async()`**, which `await`s `read_state(pool)`
directly on the caller's own loop. Same authority, same controls, no cache.
The control checks moved into a shared `_decide()` so both entry points enforce
one copy of the lane allowlist.

`_parse_switch()` now requires an actual boolean after decoding. Production
writes `'true'::jsonb` / `'false'::jsonb` via render-ops `pause-on`/`pause-off`,
so this rejects nothing real — and the legitimate-path test asserts an explicit
`false` still trades.

### 1.3 Reproduction results

`backend/tests/test_execution_gate_fail_open.py` — 26 tests. **Every test
counts mocked venue submissions and asserts zero**, because a denial that
raises the right exception while the adapter has already sent an order is not a
fix.

Against the repaired code: **26 passed**.

Against the defective code — I reintroduced all three defects and re-ran:
**13 of 26 failed**, and the 13 that failed are exactly the ones targeting a
defect. Artifact: `gate_tests_against_defective_code.xml`.

```
FAILED  TestFailedReadDoesNotAuthorize::test_the_exact_reproduction_from_the_review
FAILED  TestFailedReadDoesNotAuthorize::test_a_failed_read_immediately_after_an_allowed_read
FAILED  TestLoopThreadDoesNotAuthorizeFromCache::test_sync_authorize_on_the_loop_thread_denies
FAILED  TestKillSwitchMustBeABoolean::test_non_boolean_values_deny  [0, 1, [], {}, null, "", "false", 0.0, [false]]
FAILED  TestKillSwitchMustBeABoolean::test_parse_switch_directly
```

The failed-read-immediately-after-an-allowed-read case is explicit: the allowed
read is microseconds old and the submission is still refused. **There is no
window, not a shorter one.**

### 1.4 A test that asserted the defect, removed

`test_the_loop_thread_may_use_a_snapshot_inside_the_bound` read: *"having read
successfully a moment ago on a worker thread, an async caller may use that
answer — and only within MAX_STALE_S."* It passed, and it was wrong. It pinned
the fail-open as intended behaviour, which makes the fix look like a
regression. Replaced with
`test_the_loop_thread_never_uses_a_snapshot_however_fresh`, which asserts the
opposite and additionally asserts `authorize_async` takes its own read.

Cancellation stays ungated — re-asserted, since the repair touched the read
path every control goes through.

---

## 2. ADMISSION ACCOUNTING — the fractional intake now exists

### 2.1 The defect, reproduced

```
V3 : 1 obs/tick -> 5.333 tasks/min      (backlog=100, saturated=True)
service                                  2.667 reads/min
```

`max(ADMIT_FLOOR_OBS, int(cap.sustainable_obs_per_tick))` at 45 s / reserve 2 /
four horizons is `max(1, int(0.5))` = **1, on every tick, whatever the queue**.
1.333 obs/min creating 5.333 tasks/min against 2.667 reserved reads/min —
**twice capacity**, and twice the 0.667 obs/min I reported. The report's
fractional intake was arithmetic on paper that the code never implemented.

### 2.2 The repair

**The floor was the right answer to the wrong question.** V1 latched at zero
forever because integer division floored and nothing could reopen it. The fix
for that is not *"always admit at least one"* — a floor cannot express a rate
below one per tick — it is *"never lose the fraction"*. Those differ exactly
where it matters:

| | |
|---|---|
| permanent latch | 0 on every tick, no queue state reopens it. **The V1 bug.** |
| fractional pacing | 0 on some ticks, 1 on others, long-run average = the sustainable rate. **Correct** — one observation every two ticks at this capacity. |

`AdmissionAccount` (V5) keeps credits between ticks. Both properties are
asserted separately: `zero_ticks > 0` **and** `admitted_total > 0`.

Three things it does that V3 did not:

- **Forward obligations across all four horizons.** `outstanding_tasks` and
  `longest_horizon_s` are new: an observation admitted a moment ago owes four
  reads, none of them due yet, so `backlog_tasks` understates demand. If
  outstanding work cannot be cleared inside `longest_horizon + tolerance`
  (×`COMMITMENT_HEADROOM` 0.80), admission stops. A caller that does not supply
  `outstanding_tasks` gets the check **skipped and said so in `why`** — silently
  substituting `backlog_tasks` would be the permissive choice.
- **Bounded credits.** Carry is capped at one tick's earnings, so a long
  saturated stretch cannot bank a burst. Tested: 100 blocked ticks, then
  recovery admits ≤ 1 on the first clear tick.
- **Recovery headroom.** `TARGET_UTILISATION = 0.85`. Arrivals exactly equal to
  nominal service is a queue that never recovers from an excursion, and calling
  that stable is what the review objected to. Also `service_success_rate` — a
  **measured** input, because failed reads consume reserve without discharging
  an obligation.

### 2.3 What it actually does now

```
V5, clear queue : 0.4250 obs/tick = 0.567 obs/min = 2.267 tasks/min
service                                              2.667 reads/min
V5, backlog 100 : 0 admitted over 200 ticks
```

**Correction to the report:** implemented intake is **0.567 obs/min**, not the
0.667 I wrote. The 15% gap is the recovery headroom, deliberately reserved.

`test_a_backlog_actually_drains` initially asserted the backlog reached exactly
zero. It settles at 2 and holds. My assertion was wrong, not the code — a
collector that is admitting at all always has work in flight, and an empty queue
would mean it had stopped. The test now asserts the excursion is *removed* and
the residual *bounded*.

V3 is retained beside V1 as a regression fixture. `ADMIT_FLOOR_OBS` stays in the
module as the record of why V1 failed, and a test asserts it is **not** in the
admission path.

`backend/tests/test_admission_fractional.py` — 19 tests covering varying
capacity, measured read failures, backlog drainage, credit bounding and
recovery. All four horizons preserved.

---

## 3. CAPACITY EVIDENCE — corrected, and the corrected numbers are worse

### 3.1 The 21.9% does not follow

I took a six-hour window, computed arrivals 3.286/min and service 2.565/min,
subtracted, and called the difference work that was never serviced. Two things
are wrong with that: an observation admitted at minute 355 of a 359-minute
window owes reads at 60 s, 300 s, 900 s and 3600 s, and three of those are not
due yet; and reads completed inside the window belong to observations admitted
*before* it. **A difference of two rates over one window is not a completion
rate.**

### 3.2 The flow identity, reconciled

`research/bettor_cohort_accounting.sql`, run
[35625548145](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35625548145):

| opening | new | completed | expired | closing | inflow | outflow | **residual** |
|---|---|---|---|---|---|---|---|
| 65 | 1,168 | 908 | 278 | 47 | 1,233 | 1,233 | **0** |

Obligations are implied by missing rows — `bettor_state_mids` is written when a
read is attempted and never pre-created, and `PRIMARY KEY (observation_id,
horizon_s)` confirmed one row per task (2,802 rows, 2,802 distinct). **That also
means a retry overwrites rather than appends, so attempt counts are not
recoverable from this table** and no attempt-based rate is quoted.

PENDING and EXPIRED are separated by `last_chance = T0 + h + 30 + 600`.
Conflating them is what produced the 21.9%.

### 3.3 Deadline performance on an eligible cohort

Only observations where all four horizons have had their full chance
(`observed_at <= now() - 4230s`): 1,352 eligible, 40 too young to judge.

| horizon | obligations | read at all | never read | on-time | pct of obligations | pct of reads taken |
|---|---|---|---|---|---|---|
| 60 s | 1,352 | 802 | 550 | 216 | **15.98%** | 26.93% |
| 300 s | 1,352 | 668 | 684 | 149 | **11.02%** | 22.31% |
| 900 s | 1,352 | 617 | 735 | 153 | **11.32%** | 24.80% |
| 3600 s | 1,352 | 644 | 708 | 143 | **10.58%** | 22.20% |

**49.5% of obligations were never read**, and on-time against obligations is
10.6–16.0%. I had reported 22.9% on-time and 21.9% unserviced. **The correct
method makes both numbers materially worse.** The 6-hour flow window is kinder
(908 of 1,168 new obligations completed) than the 24-hour cohort, which is worth
stating rather than smoothing: recent hours are performing better than the day.

### 3.4 The refusal buckets prove nothing about causation

I wrote *"at ≤6 reads per tick refusals stay ≤3%"* directly beneath my own
seven-day table giving **6 reads a 7.00% refusal rate**. The claim contradicted
the evidence printed beside it.

Worse, the bucket is not an experiment. `reads_in_tick` is an **outcome** of
adaptive pacing and early stopping. Section 7 measures the confound:

| reads in tick | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| avg pacing (s) | 5.175 | 3.521 | 2.072 | 1.595 | 1.690 | 1.356 | 1.111 | 1.093 | 1.005 |
| avg skipped-budget | 1.73 | 1.57 | 0.86 | 0.64 | 2.56 | 2.60 | 2.31 | 2.15 | 2.07 |

Pacing falls monotonically with read count — the two are nearly collinear, and
low-read buckets are partly populated *by* refusals that caused a back-off.
And the 6-read bucket is 7.00% over 7 days but 0.00% over 6 hours.

**Nothing in this data establishes that seven requests cause throttling or that
six is safe.** The configuration proposal stays conditional, and the corrected
admission implementation is demonstrated offline (§2) before any bounded
production experiment is proposed.

---

## 4. RECONCILIATION — precise, and two blockers retracted

### 4.1 Retained, unchanged

The venue snapshot was a **successful read returning no holdings**:
`configured` true, no `error`, `positions` present and empty. Account identity
**unresolved**. The conclusion stays bounded to *no holdings were reported for
that account in that snapshot*.

### 4.2 Relabelled, as instructed

| | |
|---|---|
| **$16,180.53** | **unverified recorded amount** — ledger `filled_usd` across 52 rows. Not a verified acquisition cost. |
| **$8,911.89** | **unverified recorded amount** — the six mirror rows. It resembles `requested_usd` (to the cent on three rows), and **similarity to a requested amount does not establish provenance**. It is equally consistent with a request figure copied forward and with a coincidence of rounding. |
| **$15,036.19** | **recorded value associated with rows lacking a recorded closure.** Not a verified cash discrepancy, and not a loss. |

The ~30% `filled_usd` vs `filled_shares × fill_price` mismatch is ledger-wide
(70.5% of 5,267 settled rows reconcile, against 71.2% of the 52), so it says
nothing special about the unclosed rows.

### 4.3 Two of my three blockers were my own wiring

| | what I said | what is true |
|---|---|---|
| **Paginated history** | "capability gap" | `client.portfolio.activities({"cursor": …, "types": [TRADE, POSITION_RESOLUTION]})` pages fine. `pmus_account._fetch_sync` bounds it at **six pages**. That bound, not the venue, is why the card showed 25 trades. `aggregate_tennis_week`'s own docstring calls the feed "every fill and every resolution". |
| **Resting orders** | "no venue read exists" | `client.orders.list(params)` does, and `pmus.open_orders` has wrapped it since 2026-08-28. I called `/api/admin/open-orders` — a DB read of `live_orders WHERE whale_username='manual'` — and then reported the venue as lacking an endpoint. |
| **Account identity** | blocker | **still a blocker.** Unestablished either way; the probe below is built to find out rather than assume. |

### 4.4 The prepared capability — `backend/sportsassets/api/reconcile_read.py`

The smallest authenticated read-only implementation, 27 tests. Credentials stay
in the service's own environment via `pmus._get_client()`; a test asserts no
`os.environ`, no key names, no submit path, no SQL write.

- **`capability_probe()`** — *establish before coding*. Returns **key names
  only** from each payload: envelope keys, row keys, which look like identity
  fields, whether paging is supported. One page of each endpoint. Tests assert a
  balance, an account id and an order id are all absent from the result.
- **`account_identity(expected)`** — compares **inside the service** and returns
  a **verdict**: `match` / `mismatch` / `no_identity` / `unreadable` /
  `no_expected`, plus the field name used. **It never returns the identifier**,
  so reconciliation can record whether the authenticated account generated our
  fills without an account identifier reaching a CI log or an artifact. Asserted
  in every branch.
- **`historical_activity()`** — pages to eof, bounded at 200, reports `complete`
  and a stop reason. Tested to page past six; a bound hit is reported, never
  returned as a whole book.
- **`resting_orders()`** — the venue, not the manual-lane table. `pmus.open_orders`
  does `client.orders.list(params) or {}` then `.get("orders") or []`, so `None`
  and an error envelope have **already become `[]`** before a caller sees them —
  and `[]` is precisely the value that would let "no outstanding commitments" be
  written down. This raises `VenueUnreadable` for `None`, a non-mapping, a
  missing key or a non-list, and returns `count=0, ok=True` only when the venue
  said so.

### 4.5 The unexplained recent activity — explanation not narrowed

The ledger has no row of any status after 2026-09-10; the venue reported 25
trades on 2026-09-20/21 on markets the ledger has never seen. I previously gave
two explanations. That was too narrow. At least four remain open:

1. the authenticated account is not the account that generated our fills;
2. our account was traded from outside this system;
3. **incomplete logging** — the activity happened through a path that records
   nothing to `live_orders`;
4. **another execution path** — including the CLOB submission route found
   earlier in this session, which is a separate venue and a separate ledger
   label.

`account_identity()` decides between (1) and the rest. (3) and (4) are not
excluded by anything measured so far.

---

## 5. DELIVERY

Branch `claude/session-njaewf`, final SHA **`1152b97`** (this document and
its patch both include themselves). All artifacts pinned to the same reviewed
SHA**. The previous handoff named an earlier SHA than the document containing
it; `FINAL_HANDOFF.md` is superseded by this file for everything above.

| artifact | path |
|---|---|
| Repair patch (195ee01 → here) | `research/beta48/acceptance/repair_diff_from_195ee01.patch` |
| Diff stat | `research/beta48/acceptance/repair_diff_stat.txt` |
| Focused test results | `research/beta48/acceptance/repair_tests.xml` — **217 passed, 0 failures** |
| Reproductions vs defective code | `research/beta48/acceptance/gate_tests_against_defective_code.xml` — **13 of 26 fail** |
| Cohort accounting SQL | `research/bettor_cohort_accounting.sql` |
| Cohort run | [35625548145](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35625548145) |

Modules changed: `execution_gate.py`, `bettor_admission.py`; added
`api/reconcile_read.py`, `tests/test_execution_gate_fail_open.py`,
`tests/test_admission_fractional.py`, `tests/test_reconcile_read.py`,
`research/bettor_cohort_accounting.sql`.

No whole-suite sweep was run. `tests/test_pmus.py::test_resolve_direct_slug_parity`
fails both here and at 195ee01 with these changes stashed — **pre-existing and
unrelated**, verified by stashing rather than asserted.

### Status

| | |
|---|---|
| Execution gate | implementation verified against the specific reproductions; **production verification pending; undeployed** |
| Admission accounting | implementation verified offline; **not deployed**; no production experiment proposed yet |
| Account reconciliation | **one blocker remains** (identity); the other two were my wiring and the reads are now built and tested |
| Collector recommendation | **conditional** — the refusal buckets do not support a causal claim, §3.4 |
| Strategy readiness | **not assessed** (not reopened) |
