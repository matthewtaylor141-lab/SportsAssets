# Pre-deployment review of candidate `2b0fa0f`

**All three points were defects. All three are repaired. New candidate
SHA is pinned in `INCENTIVE_RELEASE_SHA.txt` beside this file.**

`2b0fa0f` is superseded and must not be deployed. Nothing has been
merged, deployed or armed; trading controls are unchanged.

---

## 1. The request ceiling

### The defect, stated plainly

`2b0fa0f` kept the count in a `RequestLedger` object, seeded from
Postgres at boot and written back **after** dispatch. That is not a
ceiling, and three ordinary events break it:

| event | what went wrong |
|---|---|
| crash **between** reservation and dispatch | the increment lived only in memory and died with the process; the next boot seeded one low and could issue the request again |
| crash **after** dispatch | the same lost increment, except the request had definitely gone out |
| **two overlapping workers** | both seeded from the same number, neither saw the other, and eight became sixteen. The supervisor makes this ordinary, not exotic: a slow shutdown and a fast restart overlap by construction |

The write-back was also justified in a comment arguing that writing
*after* dispatch was the safe direction. That reasoning was wrong: the
safe direction is to make the unit durable **before** the request can
be sent, and to lose it on a crash.

### The repair: reuse the established mechanism

Nothing new was built. `bettor_live_control.reserve()` already does
exactly this and is now used for the incentive kinds too:

- `SELECT ... FOR UPDATE` on the allowance row, so two processes
  **serialize** and cannot both read the same `used`;
- the **control** and the **deadline** are read inside that same
  transaction, so a stop landing mid-run refuses the next request
  rather than being noticed a poll later;
- the counter is incremented **before** the call returns, so a grant
  means the unit is already durable when the caller dispatches;
- tied to `probe_id`, so a process that outlived its probe answers
  `RESERVATION_PROBE_MISMATCH` and spends nothing;
- a timeout or error answers **not granted** and the caller does not
  dispatch — losing allowance is the safe direction.

`reserve()` itself is **unchanged**. Three kinds were added to its
counter/cap maps, additively:

| kind | counter | cap | value |
|---|---|---|---|
| `incentive_manifest` | `incentive_manifest_reserved` | `max_incentive_manifest` | 4 |
| `incentive_recheck` | `incentive_recheck_reserved` | `max_incentive_recheck` | 2 |
| `incentive_retry` | `incentive_retry_reserved` | `max_incentive_retry` | 2 |

**The sub-caps sum to the total: 4 + 2 + 2 = 8.** That is what makes
per-kind enforcement *total* enforcement — there is no combination of
grants reaching nine without some kind exceeding its own cap — and it
is why no cross-kind bookkeeping was added to a function the general
loop also depends on. `test_the_subcaps_sum_to_the_declared_total`
fails loudly if anyone changes one number without the other.

`DurableLedger` now holds **no counter at all**; its `report()` is a
log of grants it observed, and `read()` returns the row's own count.
The two are reported side by side precisely so they can disagree in the
open: after a restart the row is high and this boot's observations
start at zero, and that difference *is* the evidence.

The `BETTOR_INCENTIVE_HTTP_CAP` variable is gone. The caps are written
into the allowance row when the probe is armed, so the numbers approved
are the numbers enforced and no restart can widen them.

### The evidence

`research/beta48/incentive_durability_proof.py` — real PostgreSQL 16,
real `ingestion_state`, armed exactly as `obs-arm-incentive` arms it.
**16 of 16 properties passed.**

| | question | result |
|---|---|---|
| **P1a** | is the unit durable before the request goes out? | the row reads 1 **after the grant and before the dispatch** |
| **P1b** | crash between reservation and dispatch — can the lost unit be spent twice? | the row already held it; the new boot got only the 3 that remained, and the sub-cap is exactly spent at 4 |
| **P1c** | crash after dispatch — does the row still know? | yes; a fresh ledger reads 1, not 0 |
| **P1d** | **two overlapping workers** on separate pools | 16 attempts, **exactly 8 granted in total**, the row agrees, and both workers got some — they really did race |
| **P1e** | can a process that outlived its probe spend? | refused, `RESERVATION_PROBE_MISMATCH`, nothing spent |
| **P1f** | does a stop wait for the next poll? | no — refused inside the same transaction, `RESERVATION_STOPPED_BY_CONTROL` |
| **P1g** | can eight be exceeded without exceeding a sub-cap? | no; 4+2+2 == 8 |
| **P1h** | reconnect scope | declared per run; a fresh boot cannot buy another twenty |

Also pinned in CI (skipped when `BETTOR_TEST_PG_DSN` is unset, as this
repo's other PG tests are):
`test_two_overlapping_workers_share_one_eight_request_ceiling`,
`test_a_reservation_is_committed_before_the_caller_may_dispatch`,
`test_a_stop_refuses_the_next_request_inside_the_same_transaction`.

### Reconnect and resubscription limits: **per run**, not per boot

In `2b0fa0f` they were **per boot**, and that is not a bound: a worker
that crashes after twenty reconnects is restarted five seconds later
with a fresh counter, so "at most twenty" would describe a run that
made two hundred.

Now the totals live in the run row and `ReconnectBounds` is **seeded
from them**; `check()` compares prior + this boot against the ceiling
and reports both:

```
{"scope": "PER RUN, across every boot",
 "reconnects": 21, "resubscribes": 35,
 "this_boot": {"reconnects": 3,  "resubscribes": 5},
 "prior_boots": {"reconnects": 18, "resubscribes": 30},
 "max_reconnects": 20, "max_resubscribes": 40,
 "ok": false, "why": "RECONNECT_BOUND_REACHED"}
```

They remain two independent ceilings, because one reconnect
resubscribes every slug — a flapping socket and a subscribe storm are
different failures. The totals are written at each **epoch transition**
(an epoch transition *is* a reconnect, so the write is as rare as the
event) and at close.

---

## 2. Evidence durability

### The defect

The journal wrote JSONL to `BETTOR_INCENTIVE_DIR`, defaulting to
`/var/tmp/bettor-incentive`. That is an ephemeral filesystem: it passes
every fsync and loses everything at the next deploy — and a deploy is
exactly what a rollback, a configuration change or an ordinary release
performs. A `BETTOR_INCENTIVE_DIR_DISK=1` flag existed but merely
downgraded the warning.

### The production destination

**PostgreSQL, table `bettor_incentive_journal`**, created with the same
pattern `bettor_live_store` uses — one advisory lock (its own key,
`930_930_094`), bounded retries, and a **verification after any
failure** that accepts a complete schema however it arrived. This
matters because the API's migration runner and the worker boot in the
same deployment, and `CREATE TABLE IF NOT EXISTS` checks the catalog
*before* taking a lock, so concurrent creators race.

```sql
CREATE TABLE IF NOT EXISTS bettor_incentive_journal (
    id       BIGSERIAL        PRIMARY KEY,
    run_id   TEXT             NOT NULL,
    boot_id  TEXT             NOT NULL,
    at       DOUBLE PRECISION NOT NULL,
    kind     TEXT             NOT NULL,
    epoch    INTEGER,
    slug     TEXT,
    payload  JSONB            NOT NULL
);
```

Writes are batched (2 s / 200 rows) with a bounded outbox; a failed
flush **retains the batch for retry** rather than losing it, and the
bound is what stops that becoming unbounded. **The file backend is
removed, not demoted** — `Journal`, `directory_for`, `DIR_ENV` and
`DISK_ENV` no longer exist, which a test asserts.

### Recovery across process replacement

Every row carries `run_id` **and** `boot_id`. On a resumed run,
`open()` reads the previous boot's last record and writes a
`BOOT_GAP` covering `[that instant, now)` with
`why = PROCESS_REPLACED` — **before any record of the new boot**.
Without it, the only evidence of the outage would be an *absence of
rows*, and on a change-driven feed an absence of rows is exactly what
an unchanged book looks like.

### The reconstruction key is `(boot_id, epoch)`, never epoch alone

This is the trap a restart sets. `MarketStream.epoch` counts
connections **within one process** and restarts at 1 in the next one,
so "epoch 1" in boot A and "epoch 1" in boot B are different
connections with an unobserved interval between them. A reconstruction
keyed on the bare epoch would see one continuous epoch, carry boot A's
last ladder across the crash, and score the outage as a quiet book.

`segments()` keys on the pair; `covers()` answers whether an instant
was observed. Evidence (P2a–P2e, all passed):

| | question | result |
|---|---|---|
| **P2a** | where does the evidence live? | Postgres; declares itself durable; no file backend exists |
| **P2b** | do boot 1's records survive into boot 2? | both boots present; boot 1's three ladders intact |
| **P2c** | does the outage look like a quiet book? | a `BOOT_GAP` of 1.2 s was recorded, attributed to `PROCESS_REPLACED`; an instant inside it is **unobserved** and says why |
| **P2d** | is the pair the key? | both boots used epoch **1**, and they are **two** segments, keyed on `boot_id` |
| **P2e** | does gap handling throw away real coverage? | a quiet instant after boot 1's last ladder is still **observed** |

### A third gap, in the gap handling itself

`covers()` reads a GAP record's `from`/`to` to decide whether an
instant was observed. The worker's **in-run liveness gaps** carried
neither: `GAP_OPENED` cannot know the end, and `GAP_CLOSED` recorded
only `opened_at` and a duration. So a disconnect *during* a run marked
nothing, and a reconstruction would have treated the interval as a
quiet book — the same error as the boot gap, one level down. Worse,
the closing record recorded `why = "ALIVE"`, which is how the gap
ended rather than why it existed.

`GAP_CLOSED` now carries `from`, `to` and the **opening** reason, and
a gap still open when the run ends is closed with `closed_by=RUN_END`
so the final unobserved stretch cannot be read as coverage.
`test_an_in_run_liveness_gap_is_visible_to_a_reconstruction` pins both
the gap and the observed instants on either side of it.

### A second defect, found by P2e failing

My first `segments()` ended each segment at its **last ladder**. That
repeats, in the reconstruction, the very error the sampling side exists
to avoid: on a change-driven feed a quiet book sends nothing, so
ending at the final frame marks every quiet stretch before a
disconnect as unobserved — and quiet stretches are what a
qualifying-uptime measurement is made of.

A segment now runs until the **last record of that boot before its next
epoch begins**. `EPOCH` and `RUN_CLOSE` records carry that instant,
which is why they are journalled at all. Both halves are now pinned:
an instant between two boots is unobserved
(`test_an_instant_between_two_boots_is_unobserved`) and a quiet
instant inside a live epoch is observed
(`test_a_quiet_stretch_inside_an_epoch_is_still_observed`).

### Deployment

A deploy replaces the process; the table is untouched. The next boot
resumes the same `run_id`, writes its `BOOT_GAP` for the restart
interval, and continues. Coverage of the ET date is computed offline
across every boot — a boot's fraction is never reported as the date's.

---

## 3. Rollback must stop observation

### The defect

The previous package's R2 was *"delete the manifest variable; `main()`
returns to `ba87076` behaviour exactly."* That is wrong in the one case
that matters. Removing the variable returns `main()` to the **general
loop**, and if `bettor_live_observation` were still `true` that loop
would begin **discovery** — six listing pages and up to 1,680 BBO
reads. Configuration removal is not a stop.

### Repair, part one: the sequence

**Stop first. Verify. Only then remove configuration.**

```
# 1  STOP -- one UPDATE, no deploy, effective in the running process
#           within CONTROL_EVERY_S = 30s
   render-ops:  action=sql  arg=obs-stop  confirm=DO

# 2  VERIFY -- a READ. It needs no deploy and no restart.
   render-ops:  action=sql  service=sportsassets-db  arg=obs-state
   expect: false
   render-ops:  action=sql  service=sportsassets-db  arg=obs-incentive
   expect: control false; http_total_used <= 8; general_max_distinct 0

# 3  only now remove configuration
   render-ops:  action=env-del  arg=BETTOR_INCENTIVE_MANIFEST  confirm=DO
```

### The env-set / deploy distinction, accounted for

This is the 2026-09-21 finding applied to undoing rather than doing.
`BETTOR_LIVE_LOOP=off` was set, acknowledged, and survived a
demonstrated restart **without reaching the running process**; only a
new deploy delivered it.

So an `env-del` does not take effect when the API returns 200. It
takes effect when the **new process** starts. Between those two moments
the old process is still running with the old environment — still in
incentive mode, still holding its socket. That window is exactly why
the stop must come first: the stop is a **database read by the running
process**, not an environment change, and it lands in ≤30 s regardless
of any deploy.

### Repair, part two: the structural guard

Sequence discipline is not enough, because someone will one day do it
in the wrong order. `obs-arm-incentive` therefore **zeroes the general
acquisition caps** in the same row:

```
'max_distinct', 0, 'max_bbo_attempts', 0, 'max_listing_attempts', 0
```

Then a mistaken removal is harmless: `read_budget()` sees
`0 >= 0` → `BUDGET_EXHAUSTED`, `open = false`, and the general loop
returns **before a client is ever constructed**, polling at one
database read every 30 s and issuing nothing.

| | question | result |
|---|---|---|
| **P3a** | mode removed while the control is true? | the control **is** still true — removal is not a stop — but the allowance reads `BUDGET_EXHAUSTED`, listing and BBO reservations are both refused, and zero venue requests are possible |
| **P3b** | does `obs-stop` stop **both** paths? | yes — the incentive path and the general path are both refused `RESERVATION_STOPPED_BY_CONTROL`, and verification is a read needing no deploy |
| **P3c** | is the guard load-bearing? | with the general caps armed normally, the budget reads **OPEN** and a listing reservation **is granted** — which is precisely what `obs-arm-incentive` prevents |

### Rollback levels

| level | action | needs a deploy? | effect |
|---|---|---|---|
| **R0 — stop** | `obs-stop` | **no** | observation ends within 30 s, in the running process. Both paths refused. **Always first.** |
| **R1 — verify** | `obs-state`, `obs-incentive` | no | reads only |
| **R2 — remove config** | `env-del BETTOR_INCENTIVE_MANIFEST` | yes | `main()` returns to the general loop. Safe **because** R0 ran and the general caps are zero |
| **R3 — remove code** | revert the merge, push | yes | back to `ba87076` |

---

## A defect the rehearsal found that the proof did not

Worth recording, because it is the same lesson as the
`NO_CONTROL_POOL` failure of 2026-09-22.

`read_budget()` gates `open` on the **distinct-market** allowance —
correctly, since that is what decides whether the *general* loop may
acquire. The incentive arm deliberately sets `max_distinct` to zero as
the rollback guard above. So `read_budget()` answered
`BUDGET_EXHAUSTED` for the incentive run, and **the runner blocked
itself with its own safety guard**.

The durability proof did not catch it: that calls `reserve()` directly
and passed all sixteen properties. The end-to-end rehearsal through
`main()` with no arguments did not start at all. Two checks of the same
row for two different purposes need two functions, so
`read_incentive_allowance()` now asks only what the incentive run
depends on — a probe identity, an unexpired deadline, and at least one
incentive unit left — and reports `general_caps_zeroed` so the guard's
presence is visible in the same read. The runner warns loudly if the
row was armed without it.

`test_the_incentive_allowance_is_read_by_its_own_function` asserts the
worker calls the right one.

### New render-ops actions

| action | confirm | what it does |
|---|---|---|
| `obs-arm-incentive` | **DO** | arms the allowance with the 4/2/2 incentive caps, a 26-hour deadline, and the general acquisition caps **zeroed** |
| `obs-incentive` | — | read-only: control value, each incentive counter, the total used, `general_max_distinct` (must be 0), the deadline, boots, and the run's reconnect/resubscribe totals |
| `obs-incentive-journal` | — | read-only: journal rows by kind and by boot, and the last twenty recorded gaps with `from`/`to`/`duration_s` |

The journal query is a **separate action** on purpose: the table does
not exist until the first run creates it, and a status read that errors
before the run has started looks like a failure when it is not.
`obs-incentive` therefore reports whether the table exists and stops
there. Both were executed against the local server; `obs-incentive`
answers correctly with no rows at all.

A 26-hour deadline covers a 25-hour ET date plus arming slack and is
still a deadline: `reserve()` refuses past it, and `read_budget`
reports `DEADLINE_PASSED`, which disarms.

---

## The marginal production diff, against `ba87076`

```
 backend/sportsassets/bettor_incentive_budget.py    | 345 +++     NEW
 backend/sportsassets/bettor_incentive_feed.py      | 374 +++     NEW
 backend/sportsassets/bettor_incentive_journal.py   | 459 +++     NEW
 backend/sportsassets/bettor_incentive_manifest.py  | 479 +++     NEW
 backend/sportsassets/bettor_incentive_state.py     | 190 +++     NEW
 backend/sportsassets/workers/
     bettor_incentive_observe.py                    | 516 +++     NEW
 backend/sportsassets/bettor_live_control.py        | 140 ++-  existing
 backend/sportsassets/bettor_market_stream.py       |  57 ++   existing
 backend/sportsassets/workers/bettor_live_loop.py   |  45 ++   existing
 ───────────────────────────────────────────────────────────────────
 9 files, 2602 insertions(+), 3 deletions(-)
```

**The three deletions are the closing brace of three dict literals**,
and nothing else:

```
-            R_LISTING: "listing_attempts_reserved"}
-        R_LISTING: "max_listing_attempts"}
-                R_LISTING: PROBE_MAX_LISTING_ATTEMPTS}
```

Each line becomes `R_LISTING: ...,` followed by the three incentive
entries. Every existing mapping, cap and default survives unchanged,
and `reserve()`, `read_control()`, `read_budget()` and `disarm()` are
untouched. The other two pre-existing files are purely additive.

---

## Verification run

| | |
|---|---|
| durability proof (real PostgreSQL) | **16 / 16 properties** |
| rehearsal through `main()` **with no arguments** | **11 / 11 scenarios** |
| unit tests, with a DSN | **62 passed** |
| unit tests, without one | **57 passed, 5 skipped** (the PG-gated ones) |
| regression, modules touched | see below |

The rehearsal's S6/S7 now assert the repaired properties directly:
ladders under **both** epochs are in Postgres, the journal reports
`destination: postgres`, boot 2 **starts from boot 1's row total**, a
`BOOT_GAP` marks the interval nobody observed, socket bounds report
`PER RUN`, and `general_max_distinct` is 0 so no general acquisition
was even possible. S8 attempts **eleven** requests against the armed
row and gets exactly **eight**.

**The transport remains simulated** in the rehearsal — no socket, no
venue byte, and the credentials are the invented strings
`REHEARSAL-KEY-ID` / `REHEARSAL-SECRET`. What it still cannot establish
is that the venue's socket behaves as `MarketStream` assumes: that
frames are full replacements, that heartbeats arrive under that event
name, or that subscriptions are honoured at this size. The release
**records** all three rather than assuming them.

---

## Exact activation sequence

```
# ── PREPARE ──────────────────────────────────────────────────────
# 1  capture the manifest out of band (<=4 public unauthenticated
#    requests, reserved against the same armed row)
#    -> >=10 qualifying markets, else STOP: INSUFFICIENT COVERAGE

# 2  read-only preflight
   render-ops:  action=sql      service=sportsassets-db  arg=obs-state
   render-ops:  action=sql      service=sportsassets-db  arg=obs-incentive
   render-ops:  action=deploys  service=sportsassets-workers
   render-ops:  action=env-keys service=sportsassets-workers
   expect: control false; the deployed SHA; PMUS_KEY_ID + PMUS_SECRET_KEY
           present (names and lengths only)

# 3  OWNER DECISION: put the candidate SHA on the tracked branch.
#    sportsassets-api, sportsassets-workers and edge-shadow all
#    restart. Nothing observes: the control is false and the mode is
#    unconfigured.

# 4  re-read the control AFTER the deploy               [read-only]
   render-ops:  action=sql  service=sportsassets-db  arg=obs-state
   expect: false

# ── CONFIGURE ────────────────────────────────────────────────────
# 5  set on sportsassets-workers (each env-set redeploys; set them
#    together and let ONE deploy carry them):
     BETTOR_INCENTIVE_MANIFEST   = <path to the captured manifest>
     BETTOR_INCENTIVE_ET_DATE    = YYYY-MM-DD
     BETTOR_INCENTIVE_WATCH_MAX  = 12
     BETTOR_INCENTIVE_MAX_RECONNECTS   = 20
     BETTOR_INCENTIVE_MAX_RESUBSCRIBES = 40
#    The mode is now ON in the new process, but the control is still
#    false, so nothing observes.

# ── ARM ──────────────────────────────────────────────────────────
# 6  arm the allowance: 4/2/2 incentive caps, general caps ZEROED,
#    26-hour deadline
   render-ops:  action=sql  arg=obs-arm-incentive  confirm=DO

# 7  confirm the arm                                    [read-only]
   render-ops:  action=sql  service=sportsassets-db  arg=obs-incentive
   expect: http_total_used 0; general_max_distinct 0; a deadline

# ── RUN ──────────────────────────────────────────────────────────
# 8  render-ops:  action=sql  arg=obs-run  confirm=DO

# ── STOP (and the first step of any rollback) ────────────────────
   render-ops:  action=sql  arg=obs-stop  confirm=DO
   render-ops:  action=sql  service=sportsassets-db  arg=obs-state
```

Ends on whichever comes first: the ET date, the control, the socket
bounds, or an unrecoverable stream. Analysis runs once, offline, from
the journal table.

**M4 and M5 remain unexecuted. Nothing merged, deployed or armed. No
credential moved. Trading controls unchanged. No order path exists in
this release.**
