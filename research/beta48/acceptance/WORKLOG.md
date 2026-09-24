# BETTOR completion work log

Durable, append-only. Intermediate detail lives here, not in handoffs.

## W0  Standing diagnosis — SUPERSEDED, kept for the record
Originally: "88% of committed capital-hours rest behind quotes that never
fill (27,955 committed vs <=3,696 ever held; 85% of episodes never fill).
The binding constraint is the FILL RATE, not the netting call."

**This was wrong and was disproved by measurement, not by argument.**
Never-filled episodes contribute exactly 0.00 net; the entire P&L comes
from episodes that fill. Raising the fill rate (WS5, flow gate) raised
the fill rate from 39% to 78% and made the per-contract loss WORSE
(-0.0032 -> -0.0048). See W0'.

## W0' Standing diagnosis, replacement
Two costs, measured, in direct tension:
1. Completing a maker fill as a taker costs Th_taker/|Th_maker| = 4.8x
   what resting earned. On DEV: taker fees -24.98 against rebates +6.33
   and position +4.36, for a net of -14.29.
2. Not completing removes the protection against carrying a directional
   leg through resolution. On EVAL two episodes opened during live CFB
   play cost -79.82 while the other 195 netted +5.53.

The loss is the ADVERSE MOVE. The fill rate was never the lever.

## Workstreams
- WS1 shared policy code (replay + runtime), full lifecycle
- WS2 repairs: manifest capture, production integration, unequal liquidation
- WS3 decision provenance: inputs / rationale / evidence, derived vs hypothesis
- WS4 economics: chronological dev/eval split, variant register, no eval tuning
- WS5 alternative candidate, materially justified from existing research
- WS6 prespecified evaluation with dependence + selection accounting

## Entries

### WS2a manifest capture — investigated, genuinely gated
Routes tried, all closed:
- direct from container: gateway.polymarket.us 403 CONNECT (proxy status
  reports `connect_rejected … policy denial`). 2 attempts, 0 reached venue.
- `incentive-manifest.yml` (written): 404 on dispatch — workflow_dispatch
  resolves the file on the DEFAULT branch.
- `fetch-docs.yml`: on default branch, docs hosts only, published nothing.
- `beta48-capability-docs.yml`, `run85-phase2a.yml`: both hardcode
  `ref: claude/session-njaewf`, so dispatching at another ref still checks
  out the default branch and runs its script, not ours.
- reconstruct from existing evidence: P3 recorded the programme PARAMETERS
  (pools, DF, target sizes, one programId, one eventStartTime) but NOT the
  individual market slugs, so a >=10-market allowlist cannot be rebuilt.
VERDICT: gated on the deployment merge, which carries the workflow. Prepared
in full; not a reason to hold the other workstreams.

### WS1 shared policy — DONE
`backend/sportsassets/bettor_policy.py` is the one definition. The replay
imports it (`epi.shared is bp`, asserted) and every decision it used to
inline now calls it: entry/admission, sizing, cancel-other-on-fill, the
quoting horizon, release-vs-hold, and the four-way recovery dispatch.
`bettor_policy_runtime.py` is the live-side adapter; the engine still
governs and the policy only proposes.

Regression: bettor_strategy_v2 reproduced its prior numbers exactly
(V1 deltas +3.85 / +1.89 / -18.08 / +14.20), so the rewiring changed no
behaviour. 38 policy tests + 84 engine/adapter tests pass.

Defect found BY RUNNING IT: `admit()` recognised only the venue's
`MARKET_STATE_OPEN`, but every Book from the runtime carries the
normalised `OPEN`. The live engine would have stood aside on every
market and reported a market-state refusal indistinguishable from a
quiet day. `is_open()` now holds the translation in one place.

### WS2 repairs
- manifest capture: gated (see WS2a). Prepared in full.
- production integration: the state-vocabulary seam above; fixed.
- unequal liquidation: fixed in `_release_pair` — measure what the YES
  sale actually returned, ask NO for exactly that, re-pair the residue,
  and append RELEASE_INCREASED_DIRECTIONAL_EXPOSURE if net exposure grew.

### WS3 provenance — DONE
`RULES` labels each rule DERIVED or HYPOTHESIS with inputs, rationale and
evidence. Derived: entry, placement, inventory, inventory_cap, recovery.
Hypotheses: entry_band, sizing, release, flow, volatility.

### WS4/WS6 prespecified evaluation — DONE, artifacts written
Cut 2026-09-17T00:00Z, chosen from row counts and dates alone (DEV
15,385 rows / EVAL 15,205). Protocol committed in 851cb23 BEFORE the
split was read. 15 variants registered, 60 DEV looks, ONE eval touch.
Intervals are bootstraps over EVENTS resampled whole — 420 episodes over
five events are five observations.

Protocol amendment, made on DEV and recorded: "best worst-case net" is
degenerate (its optimum is to trade nothing; a 4-episode variant won
it). Activity floor >=30 episodes and >=3 events in every scenario.

RESULT: does not qualify. R10 on EVAL, -74.29 / -72.37 / -80.81 /
-100.01 with every clustered interval excluding zero on the negative
side. DEV had shown -3.29 worst case. See ECONOMIC_VERDICT_V2.md.

`bettor_evaluation.py` now REFUSES a second eval touch in code.

### WS5 alternatives — two attempted
- FLOW GATE (R4-R6): failed, kept as a negative result, not retuned.
- TAKER AVOIDANCE (R7-R11): best on DEV, catastrophic on EVAL, and the
  reason is exactly identified.
- VOLATILITY GATE (R12-R14): DEV ONLY. Refuses both loss episodes by
  7x-12x, threshold-insensitive. Cannot be validated here — the eval
  split is spent and the DEV period contains none of the volatility the
  rule targets. Prespecified for fresh data.

### Open, and genuinely blocked on data
The remaining experiment needs a capture containing live event
resolution and >=20 independent events (~a month at the current rate).
The observation release is the instrument, and the incentives API it
reads carries `eventStartTime` — the time-to-resolution input the
captured corpus lacks entirely.

### Correction found after the verdict was written
The DEV/EVAL cut (2026-09-17T00:00Z) sits FOUR HOURS before the PMUS
taker regime change (0.0600 -> 0.0695 at 2026-09-17T04:00Z). Both dates
come from the calendar and neither was picked with reference to the
other, but DEV therefore runs almost entirely under the old rate and
EVAL under the new one. The replay applies the regime BY FILL TIMESTAMP
so every number is arithmetically right; the DEV-to-EVAL COMPARISON is
what carries the confound. It cannot explain the result: EVAL's taker
fees were -21.81 against a position loss of -63.91, and 16% of 21.81 is
about $3 of a $74 loss. Recorded in the protocol docstring and in
ECONOMIC_VERDICT_V2.md rather than left for a reader to find.

### Manifest capture — re-verified this session, still gated
gateway.polymarket.us: 403 CONNECT from this container, confirmed again.
NEW finding: `beta48-forward-capture.yml` and
`beta48-substantive-capture.yml` ARE on the default branch and DO reach
the venue, so Actions runners have egress. But each runs a FIXED set of
script names; `beta48-substantive-capture` checks out an arbitrary
`code_sha` yet still invokes substantive_select.py / capture_manifest.py
/ substantive_capture.py by name, so a new file at my SHA would never
run, and repurposing a frozen capture script is the unrelated
modification the standing restrictions forbid.
Two unblocking routes, in APPROVAL_REQUEST_V3.md. Recommended: allow
gateway.polymarket.us in the environment network policy (smallest, no
repository effect, no service restart).

### MANIFEST CAPTURED — the route worked, and the earlier blocker report was wrong
Run 35788331780, dispatched at commit 30c7ae1, conclusion SUCCESS in 35s.
441 programme rows over 4 pages, zero errors, unauthenticated. Freeze OK:
12 markets, 1 programme (culture_low_20260921), 1 event start time.
Manifest commit 5cfd211.

WHAT I GOT WRONG. I reported "every venue-reaching workflow runs a fixed
set of script names, so a new file at my SHA would never execute" as an
external blocker. The relevant fact is different: workflow_dispatch
resolves the workflow by ID against the DEFAULT branch, but RUNS THE FILE
AS IT EXISTS AT THE DISPATCHED REF. So extending an already-dispatchable
workflow on the release branch was available the whole time.

ALLOWANCE: cap 6, was 2 spent, the run spent the remaining 4 (one per
page). Now 6/6, remaining 0. Durable in preflight_allowance.json, so a
re-dispatch is refused rather than restarted.

### EVALUATION PROVENANCE — independent-holdout claim WITHDRAWN
acceptance/policy_final.json sweeps C0/C2/C3/C4 x 4 queue fractions over
the WHOLE tape, including 17-20 September, and the C3 baseline in the
register was selected using it. A protocol committed on 2026-09-22 does
not make earlier-inspected dates untouched.

So: chronological DEVELOPMENT split, every figure a DEVELOPMENT
DIAGNOSTIC, no out-of-sample claim. "Confident negative" withdrawn --
five clusters do not settle the mechanism in either direction, and a
"confident positive" would have been wrong the same way.

WHAT SURVIVES INDEPENDENTLY: the fee decomposition. Th_taker/|Th_maker|
= 4.8 / 5.6 is arithmetic on the published schedule, and the per-episode
cash split is an accounting identity over observed fills.

### eventStartTime IS NOT A RESOLUTION TIMESTAMP — claim withdrawn
The captured value is 2026-12-27T04:59Z, three months past the
observation window, on year-end album markets. It is the programme's
event reference and its meaning is not constant across categories. The
previous handoff's claim that the observation release supplies the
missing time-to-resolution input is withdrawn. See OBSERVATION_SCOPE.md.

### Full-suite result, and what was actually established
445 failed / 10674 passed / 143 skipped in 24 minutes. Sampled the
reported failures (test_render_ops_take_band, test_shadow_v2,
test_workers_boot_stagger, test_s4_review_pins) in isolation: 6 failed /
127 passed. Ran the SAME selection on ba87076, the production SHA, with
none of this work present: IDENTICAL 6 failed / 127 passed. Those are
pre-existing.

NOT ESTABLISHED: attribution of all 445. That needs a full baseline run,
which is broad testing and was not authorized. What is established is
that the sampled failures are not caused by this work.

### Image verification, and a misleading refusal found by it
Built from 0228b10 and verified INSIDE the container: manifest loads
through the real startup path (441 programs, et_date 2026-09-22,
authenticated False), freeze OK at 12 markets / 1 programme / 1 event,
and the runtime adapter produces QUOTE_BOTH_SIDES -> engine
PROPOSED_BUT_NOT_SCORED -> NO_TRADE 0.0, executable False.

A DEFECT FOUND BY BEING MISLED BY IT. load() returns a WRAPPER
{ok, why, path, manifest}; passing that wrapper to freeze() returned
MANIFEST_HAS_NO_QUALIFYING_PROGRAMS. Fail-closed, and for entirely the
wrong reason -- it reads as "the venue had no programmes today". I spent
a build and three checks hunting a delivery defect that did not exist;
the file in the image was byte-identical to the host (277,118 bytes,
same md5). freeze() now refuses a document with no `programs` key BY
NAME and says which value to pass. Pinned by a test.

### Approval request V4 written, release SHA 0228b10
Scope: three services, one variable, one observation day. Preflight
allowance EXHAUSTED (6/6). Socket allowances separate. Rollback is
stop-and-verify first.

### 2026-09-23 MANIFEST CAPTURED — allowance raised, not reset
Run 35793974775, dispatched at cf29391 (a branch taken from DEPLOYED
d630d3d, so 67d7a93 and every other release-branch change stayed out).
SUCCESS. 441 programme rows, 4 pages, zero errors, unauthenticated.

  allowance   cap 10, spent 10, remaining 0
              the four new units went one per page, exactly as sized
  freeze OK   12 markets / 1 programme / 1 event
  programme   culture_low_20260921, pool $50, DF 0.25, target 500

THE PROGRAMME IS THE SAME ROW AS YESTERDAY, and that settles F2's
ambiguity in the direction the venue docs already stated. start is
still 2026-09-22T00:00:00Z and end is still null on today's capture --
an unchanged, continuing programme record, NOT a per-day period row. So
`start` is the programme ACTIVATION instant, not a daily scoring
boundary, and the daily period is the venue's documented
midnight-to-midnight ET. The window [2026-09-23T04:00Z,
2026-09-24T04:00Z) is correct. API start/end preserved verbatim; no
widened window.

### DEPLOYED 7f76fd9 — and [skip render] nearly hid it
d630d3d..7f76fd9 is exactly two data files: incentive_manifest.json and
preflight_allowance.json. Fast-forward from the deployed SHA. 67d7a93
verified absent.

THE CAPTURE COMMIT CARRIES `[skip render]`, which the capture workflow
adds on purpose so evidence commits do not auto-deploy. Pushing it to
the auto-deploy branch therefore did NOT deploy it: workers still read
d630d3d from 22:21Z a minute after the push. Caught by reading the
deploy list instead of assuming the push was the deployment. Resolved
with an explicit render-ops `deploy` dispatch on both services.

### ARM SCHEDULED, not executed early
obs-arm-incentive sets deadline = now + 26h. Arming now (22:53Z) would
give a deadline of 00:53Z on 09-24, which does NOT cover the window end
at 04:00Z on 09-24. The valid arm window is therefore
[2026-09-23T02:00Z, 04:00Z].

Separately, the worker has NO gate on the window START -- `start_epoch`
appears once, in the mid-run recheck timing, and the loop only breaks
on `now >= end_epoch`. Armed early it would connect and collect hours
before 04:00Z, spending socket allowance outside the window. Adding a
start gate would be an unrelated code change, so the arm is TIMED
instead: scheduled for 03:50Z, trig_012jP2v4wuEhiVatwybx6MAc.

### 2026-09-23T01:12Z — PRE-START VERIFICATION, and the deadline blocker
Verified before touching anything:

  deployed       7f76fd9 live on sportsassets-api and sportsassets-workers
  control        false  -- NOT observing
  http_total     0 of 8
  socket         null / null  (row has no socket fields -- see below)
  general cap    max_distinct 40   <- GENERAL-loop shape, not the arm shape
  deadline_at    2026-09-22T14:35:09Z  -- STALE, expired ~10.6h ago
  journal table  absent -- no run has started
  preflight      10/10 spent, 0 remaining

VERDICT: UNARMED. The probe row is a leftover from a prior session, with
the general-loop cap of 40 rather than the incentive arm's zero, and an
expired deadline. So this is the "arm exactly once" case.

THE DEADLINE BLOCKER, stated precisely.
`render-ops` action=sql accepts ONLY named statements from a fixed bash
`case` list -- there is no parameter and no arbitrary-SQL path. The
`obs-arm-incentive` statement hardcodes

    'deadline_at', to_char((now() + interval '26 hours') ...)

so an EXPLICIT deadline of 2026-09-24T04:00:00Z cannot be set through
the existing mechanism. Arming at 01:12Z would have produced
2026-09-24T03:12:00Z -- 48 MINUTES SHORT of the required window end,
truncating the measurement window at its tail.

THE RESOLUTION, which needs no code change and no deployment:
now+26h >= the window end exactly when now >= 2026-09-23T02:00:00Z.

    arm 01:12Z -> 2026-09-24T03:12Z   SHORT by 48 min
    arm 02:00Z -> 2026-09-24T04:00Z   covers, zero margin
    arm 02:10Z -> 2026-09-24T04:10Z   covers, +10 min  <- CHOSEN

So the arm waits 58 minutes rather than being forced through a mechanism
change. The cost is 58 minutes of EARLY collection, not any of the
measurement window. Early collection is [02:10Z, 04:00Z), ~1.83h, and it
is operational evidence only -- excluded from the economic window's
coverage and reward calculations.

Both pending triggers DISABLED so neither can duplicate or reset this
run: trig_012jP2v4wuEhiVatwybx6MAc (03:50Z arm) and
trig_01CiGjRDEb9mTjihzYPJqZov (04:00Z start). Replaced by a single
trig_0156xerpzsYKoJLkDvvv2PxT at 02:10Z that arms once and starts, with
an idempotency guard that refuses to re-arm a row already in the armed
shape with a sufficient deadline.

---

## CHECK 1 — 2026-09-23T13:49Z–13:51Z. STILL COLLECTING.

Read from durable records only, via `render-ops` at ref
`claude/command-center`. `claude/session-njaewf` verified still at
`7f76fd9`.

### Control (`obs-incentive`, run 35869755487, read 13:49:29Z)

| field | read | required | |
|---|---|---|---|
| control | **true** | true | ✓ |
| http_total_used | **0** | against 8 | ✓ |
| general_max_distinct | **0** | must be 0 (the arm's shape) | ✓ |
| sock_connects | **2** | against 20 | ✓ |
| sock_subs | **4** | against 40 | ✓ |
| deadline_at | **2026-09-24T04:35:48+00:00** | unchanged | ✓ |
| boots / reconnects / resubscribes | 2 / 1 / 2 | | |

**`probe_id` is not returned by `obs-incentive`.** It was read instead
from `obs-live` (run 35869867079, 13:50:24Z), which exposes it
read-only:

    armed_probe_id = d5e9ae3d-257f-4948-a808-90d1bd3c5e48   UNCHANGED

So the stop-and-report condition is **not** triggered. Nothing re-armed.

The same read shows `bettor_live_journal` at **84,831 s** old with 0
rows in the last 60 s. **That is not the collector.** It is the GENERAL
loop's journal, which is idle by design while the incentive arm runs;
the arm writes `bettor_incentive_journal`. Recorded here so a later
reader does not mistake an idle general loop for a dead collector.

### Journal (`obs-incentive-journal`, run 35869799788, read 13:49:51Z)

| kind | rows | first | last |
|---|---:|---|---|
| LADDER | **5,858** | 10:28:29Z | **13:49:51Z** |
| EPOCH | 4 | 10:28:29Z | 10:31:15Z |
| GAP | 6 | 10:28:29Z | 10:31:15Z |
| PROGRAM_VERSION | 2 | 10:28:29Z | 10:31:14Z |
| RUN_OPEN / RUN_CLOSE | 2 / 1 | | |

**The max LADDER `at` is 13:49:51Z — the same second as the query.**
The journal is growing. This is the reading to compare the next check
against.

| boot_id | rows | first | last |
|---|---:|---|---|
| `7435b23a98d049f3` | 43 | 10:28:29Z | 10:28:59Z |
| `8702807518fe44b4` | **5,830** | 10:31:14Z | **13:49:51Z** |

Gaps, all three with from/to:

| why | duration | when |
|---|---:|---|
| `PROCESS_REPLACED` | **135.191 s** | 10:28:59Z → 10:31:14Z |
| `GAP_DISCONNECTED` | 0.5268 s | 10:28:28Z |
| `GAP_DISCONNECTED` | 0.5003 s | 10:31:14Z |

**Total recorded gap 136.218 s.** The `PROCESS_REPLACED` is the worker
restart at the deploy changeover, recorded as an event, not treated as
a fault.

### Markets (`obs-incentive-markets`, run 35869937056, read 13:51:02Z)

    markets_receiving     12 of 12
    markets_with_depth    12 of 12
    frames_total          5,921
    frames_in_window      5,921
    frames_before_window  0

Per-market frames range 139 (`chaxcx`) to 1,063 (`eminem`); every one
of the twelve has depth on every frame, max ask levels 6–23.

### The API deploy at 13:46Z did NOT disturb the collector

An API-only release (`2d7ed3f`, `dep-daptgg9srm7s73aq624g`, live
13:47:37Z) went out between the last check and this one. Three
independent confirmations that the collector was untouched:

1. the deploy action printed the worker deploy list **before and
   after** and it is byte-identical (`dep-dapqirrbc2fs73bms6fg live
   7f76fd9` both times);
2. `claude/session-njaewf` — the branch both services track — is still
   at `7f76fd9`, so no auto-deploy fired;
3. **the journal itself**: no GAP and no new boot after 10:31:14Z, and
   LADDER rows continue through 13:49:51Z. A worker restart at 13:46Z
   would have written a `PROCESS_REPLACED` gap and a third boot_id.
   Neither exists.

### THE DENOMINATOR, stated before anyone quotes a coverage number

The measurement window opened at **04:00:00Z**. Collection began at
**10:28:29Z**.

    unobserved head   23,309 s = 6.47 h
                      65.9% of the window elapsed so far
                      27.0% of the full 24 h window
    elapsed           9.83 h of 24.00 h

**This is not a complete-day observation and must not be reported as
one.** The first 6.47 hours are unobserved and belong in the
denominator of every coverage and reward figure computed from this run.

The stop at 2026-09-24T04:00:00Z (`trig_01RKm5XcVvaUXCqLgbAnV2T6`) is
unchanged. No counter was reset and nothing was re-armed.

---

## Observation verification CHECK 2 — 2026-09-23T17:30:24–17:30:58Z

Durable records only, dispatched at `ref=claude/command-center`. Nothing
re-armed, no counter reset, the stop untouched.

**Collection is live and growing.** The LADDER journal has more than
doubled since check 1 and its newest row is 5 seconds old at read time.

| | check 1 (13:49–13:51Z) | check 2 (17:30Z) | |
|---|---|---|---|
| LADDER rows | 5,858 | **14,662** | +8,804 |
| LADDER max `at` | 13:49:51Z | **17:30:29Z** | +3 h 40 m |
| boots | 2 | **2** | no third boot |
| gaps carrying from/to | 3 | **4** | **one new** |
| total gap | 136.218 s | **137.7235 s** | +1.5055 s |
| markets receiving | 12 of 12 | **12 of 12** | |
| markets with depth | 12 of 12 | **12 of 12** | |
| frames in window | 5,921 | **14,666** | |
| frames before window | 0 | **0** | |
| control | true | **true** | |
| `general_max_distinct` | 0 | **0** | general loop still idle |
| http | 0 of 8 | **1 of 8** | one recheck |
| socket connects | 2 of 20 | **3 of 20** | |
| socket subscribes | 4 of 40 | **6 of 40** | |
| `deadline_at` | 2026-09-24T04:35:48+00:00 | **unchanged** | |
| `probe_id` | d5e9ae3d-257f-4948-a808-90d1bd3c5e48 | **unchanged** | no stop condition |

**THE ONE NEW GAP.** `GAP_DISCONNECTED`, from `1790173110.146661` to
`1790173111.6521392`, **1.5055 s**, at approximately **14:18:30Z** —
which agrees with the EPOCH and GAP rows' newest `at` of 14:18:32Z. It is
a disconnect-and-recover, not a process replacement: `boot_id`
`8702807518fe44b4` is unbroken from 10:31:14Z to 17:30:29Z and carries
14,638 of the 14,681 journal rows. The socket counters corroborate it —
connects 2 → 3, subscribes 4 → 6, with `run_reconnects` 2 and
`run_resubscribes` 4.

**One thing check 1 did not record, noted now so check 3 has a baseline:**
`PROGRAM_VERSION` stands at **3 rows, newest 16:00:00Z**. I cannot say
whether that third row is new since check 1 because the count was not
captured then. It is a version stamp, not a gap, and no gap or boot
accompanies it.

**All 12 markets are individually live**, newest per-market rows spanning
17:28:57Z → 17:30:34Z. None has gone quiet.

### Coverage, with the unobserved head in the denominator

The window opened **04:00:00Z**; collection began **10:28:29Z**. So
**6.475 h is unobserved before the run started** and stays in the
denominator.

- Full window: 24 h (04:00Z → 04:00Z)
- Elapsed at this read: 13.508 h
- Observed span: 10:28:29Z → 17:30:29Z = 7.033 h, less 137.7 s of gaps
  = **6.995 h**
- **Coverage of elapsed window: 51.8%.** Of the full 24 h: 29.1% so far.

This is **not** a complete-day observation and must not be reported as
one. 27.0% of the full window was already unobservable before the first
frame arrived.

Check 3 is armed. Check 1 disabled itself and left fourteen hours
unwatched; each check now leaves its successor scheduled.

---

## Observation check 3 — read 21:13:46Z / 21:14:55Z

Two reads, both from durable records via `render-ops` on ref
`claude/command-center`: `obs-incentive` (run 35921099203) and
`obs-incentive-journal` (run 35921219616).

### Control and budget — every figure UNCHANGED since check 2

| | check 2 (17:30Z) | check 3 (21:13Z) | |
|---|---|---|---|
| `bettor_live_observation` | true | **true** | still collecting |
| http total used | 1 | **1** | recheck only |
| socket connects | 3 | **3** | |
| socket subscribes | 6 | **6** | |
| `boots` / `reconnects` / `resubscribes` | 2 / 2 / 4 | **2 / 2 / 4** | |
| `deadline_at` | 2026-09-24T04:35:48+00:00 | **unchanged** | not moved |

**No new disconnect in three and a half hours.** The four counters that
would have recorded one did not move.

### Journal

| kind | rows | newest |
|---|---|---|
| LADDER | **26,756** (was 14,662) | **21:14:45Z**, ten seconds before the read |
| GAP | 8 (4 carry a `from`) | 14:18:32Z |
| EPOCH | 5 | 14:18:32Z |
| PROGRAM_VERSION | **3** | **16:00:00Z** |
| RUN_OPEN / RUN_CLOSE | 2 / 1 | 10:31:14Z / 10:28:59Z |

**+12,094 LADDER rows** since check 2, and the newest is ten seconds old:
the collector is live at the moment of reading, not merely un-stopped.

**No new gap.** The newest is still the 1.5055 s `GAP_DISCONNECTED` at
~14:18:30Z that check 2 reported. All four, oldest last: 0.5268 s,
135.191 s `PROCESS_REPLACED`, 0.5003 s, 1.5055 s — **137.7236 s total**,
unchanged.

`boot_id 8702807518fe44b4` is now unbroken from **10:31:14Z to
21:14:45Z** and carries 26,732 of the 26,775 journal rows.

**Check 2's open question is answered.** `PROGRAM_VERSION` still stands at
3 rows, newest 16:00:00Z — so the third row was already there at check 2
and nothing has stamped a version since. It was not a gap then and there
is no new one now.

### Coverage, with the unobserved head still in the denominator

- Full window: 24 h (04:00Z → 04:00Z)
- Elapsed at this read: **17.246 h**
- Observed span: 10:28:29Z → 21:14:45Z = 10.771 h, less 137.72 s of gaps
  = **10.733 h**
- **Coverage of elapsed window: 62.2%** (was 51.8%)
- Of the full 24 h: **44.7%** so far

The **6.475 h before collection started stays in the denominator** and
always will. Even a flawless remainder cannot make this a complete-day
observation: 27.0% of the window was unobservable before the first frame.

Check 4 is armed. The stop at 2026-09-24T04:00:00Z has not moved.

### Check 3, steps 2 and 4 — completed at 21:58:46Z and 22:00:06Z

My first write-up of check 3 covered only steps 1 and 3. The check
specifies four readbacks and I had run two. The missing two, run now:

**Step 2 — `obs-live` (run 35925670080, 21:58:46Z). The stop condition did
not fire.** `armed_probe_id` is still
**`d5e9ae3d-257f-4948-a808-90d1bd3c5e48`**, unchanged since check 1, so the
arm was never replaced. `observation_control` true; `bbo_attempts_reserved`,
`listing_attempts_reserved` and `distinct_reserved` all **0**.

`bettor_live_journal` reads 14 rows, newest **2026-09-22 14:16:33Z**, age
**114,133.5 s** (31.7 h), 0 rows in the last 60 s. **That is the GENERAL
loop and it is idle by design while the arm runs** — reading its age as
collector silence would be a category error. All 14 of its rows are
`OBSERVATION_ONLY`, 0 `STRATEGY_ADMITTED`, across 8 markets, newest
`boot_id 3ddd79c916e54190` — a different boot lineage from the incentive
run's, which is exactly why the two must not be read as one instrument.

**Step 4 — `obs-incentive-markets` (run 35925793462, 22:00:06Z).**

| | check 2 (17:30Z) | check 3 (22:00Z) |
|---|---|---|
| `markets_receiving` | 12 of 12 | **12 of 12** |
| `markets_with_depth` | 12 of 12 | **12 of 12** |
| `frames_total` | 14,666 | **27,838** |
| `frames_in_window` | 14,666 | **27,838** |
| `frames_before_window` | **0** | **0** |

**No market has gone quiet.** Every one of the 12 has depth on every frame
it recorded (`frames == with_depth` for all twelve), each starts at
10:28:29Z, and the newest per-market frames span **21:59:07Z → 22:00:03Z** —
all inside a minute of the read. Frame counts are very uneven (benboo
7,193, coldpl 5,238, chaxcx 509), which is market activity, not coverage:
the quietest market is as continuously subscribed as the busiest.

`frames_before_window` is **0** again — nothing outside the window has
leaked into the denominator.

**One correction to the check's own preamble:** it states `render-ops.yml`
is 511,233 bytes. It is **511,578** as of `44a350c`, which added a
non-2xx guard to `deploy-api-commit`. Still **422 bytes under** the
512,000 ceiling, measured directly rather than taken from the note.

---

## CHECK 4 — 2026-09-24 01:46–01:48Z · all four readbacks, from the job logs

Dispatched at `ref=claude/command-center`. `render-ops.yml` = **511,578 bytes**
(unchanged; last touched by 44a350c), so no startup_failure risk. Every
figure below is read out of the psql output in the job log, not from a
workflow conclusion.

### 1 · `obs-incentive` — run 35944359134

| field | value | check |
|---|---|---|
| control | **true** | ✓ still observing |
| general_max_distinct | **0** | ✓ general loop still not sampling |
| manifest / recheck / retry | 0 / 2 / 0 → **2 total** | ✓ inside 8 / 20 / 40 |
| sock_connects / sock_subs | 7 / 14 | within allowance |
| deadline_at | **2026-09-24T04:35:48+00:00** | ✓ unmoved |
| boots / reconnects / resubscribes | **3** / 4 / 8 | boots up from 2 — see §3 |

### 2 · `obs-live` — run 35944387072

**`armed_probe_id` = `d5e9ae3d-257f-4948-a808-90d1bd3c5e48` — UNCHANGED.**
No STOP condition.

`bettor_live_journal` is 14 rows, newest 2026-09-22 14:16:33Z, age 127,798 s,
boot `3ddd79c916e54190`, 8 distinct markets, 14 OBSERVATION_ONLY / 0
STRATEGY_ADMITTED. That is the GENERAL loop and it is idle by design while
the arm runs, exactly as check 4's instructions state. Its age is not
evidence about the incentive collector.

### 3 · `obs-incentive-journal` — run 35944416187 · COLLECTION IS ALIVE

| kind | rows | first | last |
|---|---|---|---|
| LADDER | **34,007** | 10:28:29Z | **2026-09-24 01:46:54Z** |
| GAP | 17 | 10:28:29Z | 01:34:51Z |
| EPOCH | 10 | 10:28:29Z | 01:34:51Z |
| PROGRAM_VERSION | **5** | 10:28:29Z | 2026-09-23 22:58:58Z |
| RUN_OPEN | 3 | 10:28:29Z | 22:58:58Z |
| RUN_CLOSE | 1 | 10:28:59Z | 10:28:59Z |

Both liveness floors cleared: LADDER **34,007 > 26,756**, newest `at`
**01:46:54Z > 21:14:45Z**. A true control flag over a static journal would
have been the failure; this is not that.

**A THIRD boot_id, as check 3 anticipated:**

| boot_id | rows | first | last |
|---|---|---|---|
| `7435b23a98d049f3` | 43 | 10:28:29Z | 10:28:59Z |
| `8702807518fe44b4` | 29,243 | 10:31:14Z | **22:58:35Z** |
| **`d61606169a1f410c`** | **4,757** | **22:58:58Z** | 01:46:54Z |

So `8702807518fe44b4` is no longer unbroken — it ended at 22:58:35Z and a
new boot took over 23 s later. PROGRAM_VERSION moved from 3 rows to 5, and
past 16:00:00Z to 22:58:58Z.

**NEW GAPS: 9 rows carry a `from` (was 4); 17 GAP rows total (was 8).**
Five are new, and the total duration is now 164.4374 s (was 137.7236 s):

| why | from (UTC) | duration_s | new? |
|---|---|---|---|
| PROCESS_REPLACED | 2026-09-23 22:58:34Z | **22.665** | **NEW** |
| GAP_DISCONNECTED | 2026-09-23 22:58:57Z | 0.5189 | NEW |
| GAP_DISCONNECTED | 2026-09-24 00:02:59Z | 1.0185 | NEW |
| GAP_DISCONNECTED | 2026-09-24 00:10:26Z | 1.5107 | NEW |
| GAP_DISCONNECTED | 2026-09-24 01:34:50Z | 1.0007 | NEW |
| GAP_DISCONNECTED | 2026-09-23 14:18:30Z | 1.5055 | prior |
| GAP_DISCONNECTED | 2026-09-23 10:31:14Z | 0.5003 | prior |
| PROCESS_REPLACED | 2026-09-23 10:28:59Z | 135.191 | prior |
| GAP_DISCONNECTED | 2026-09-23 10:28:28Z | 0.5268 | prior |

**WHAT CAUSED THE 22.665 s PROCESS_REPLACED — and what did NOT.** It is
NOT one of this session's deploys. Six API deploys went out after it
(23:53:49, 01:03:48, 01:06:0x, 01:12:0x, 01:32:0x, 01:45:2x) and **none**
produced a PROCESS_REPLACED, so the incentive collector does not run in
`sportsassets-api`'s process. The worker service's live deploy is still
`7f76fd9` from 2026-09-23T10:26:55Z and was read as unchanged before and
after every one of those deploys. On this evidence it is a platform-side
instance recycle at 22:58:34Z; the cause is not established from these
four readbacks and is NOT attributed further. Nothing was re-armed and no
counter was reset.

### 4 · `obs-incentive-markets` — run 35944484582

| | check 3 | check 4 |
|---|---|---|
| markets receiving | 12 of 12 | **12 of 12** |
| markets with depth | 12 of 12 | **12 of 12** |
| LADDER frames | 27,838 | **34,045** (+6,207) |
| frames in window | all | **34,045, all** |
| frames before window | 0 | **0** |

Newest per-market spans **01:46:07Z → 01:47:55Z** — every one of the twelve
is inside the last two minutes. **No market has gone quiet.** Depth ladders
remain populated: max ask levels 6–23, max bid levels 2–9.

### Coverage — the unobserved head stays in the denominator

```
window opened            2026-09-23T04:00:00Z
first frame              2026-09-23T10:28:29Z
unobservable head        6.4747 h
newest frame             2026-09-24T01:47:55Z
span first->newest      15.3239 h
gap total                  164.4374 s = 0.045677 h
OBSERVED                15.2782 h
elapsed since open      21.7986 h
```

**70.1% of elapsed, 63.7% of the full 24 h.** (Check 3: 62.2% / 44.7%.)
This is NOT a complete-day observation and must never be reported as one:
6.47 h at the head were never observable, and 164 s were lost to gaps.

### Verdict

Collection is running and has been continuous since 22:58:58Z under boot
`d61606169a1f410c`. Control true, probe id unchanged, allowance barely
touched, deadline unmoved, no market silent. No diagnosis was required and
no remedial action was taken.

**CHECK 5 armed for ~03:50Z, same four readbacks.** The stop at
2026-09-24T04:00:00Z (`trig_01RKm5XcVvaUXCqLgbAnV2T6`) has not been moved.

---

## CHECK 5 — 2026-09-24T03:51Z — THE FINAL CHECK BEFORE THE STOP

Four readbacks, dispatched at `ref=claude/command-center`, psql output read
from each job log. `render-ops.yml` measured first: 511,578 bytes, unchanged.

### 1 · obs-incentive (run 1518, job 107485843603)

```
control true   manifest_used 0   recheck_used 2   retry_used 0   http_total 2
general_max_distinct 0   sock_connects 7   sock_subs 14
deadline_at 2026-09-24T04:35:48+00:00   boots 3   reconnects 4   resubscribes 8
```

Identical to check 4 on every field. Allowance inside 8/20/40 and sockets
inside 7/14. **`boots` has NOT moved past 3.** Deadline unmoved.

### 2 · obs-live (run 1519, job 107485866224)

`armed_probe_id d5e9ae3d-257f-4948-a808-90d1bd3c5e48` — **unchanged**, so no
stop-and-report condition. `observation_control true`.
`bettor_live_journal` still 14 rows, newest 2026-09-22 14:16:33Z, boot
`3ddd79c916e54190`, 0 rows in the last 60 s: the GENERAL loop, idle by
design, as check 5's own instruction states.

### 3 · obs-incentive-journal (run 1520, job 107485870524)

```
LADDER  37196 rows   newest 2026-09-24 03:51:23Z     (check 4: 34007 / 01:46:54Z)
EPOCH      10        GAP 33        PROGRAM_VERSION 5 rows, last 22:58:58Z
boots 3   runs 1
7435b23a98d049f3     43 rows   10:28:29Z -> 10:28:59Z
8702807518fe44b4  29243 rows   10:31:14Z -> 22:58:35Z
d61606169a1f410c   7964 rows   22:58:58Z -> 03:51:25Z
```

LADDER exceeds 34,007 and the newest `at` exceeds 01:46:54Z, both as
required. **No fourth boot_id: still three, and the third is still
`d61606169a1f410c`.** PROGRAM_VERSION has **not** moved past 5 rows /
22:58:58Z.

**EIGHT NEW GAPS, all `GAP_LIVENESS_UNDETERMINED`, totalling 581.4008 s**
(check 4 had 9 rows with a `from` totalling 164.4374 s; there are now 17):

| from (epoch) | duration s |
|---|---|
| 1790217768.38 | 160.5482 |
| 1790217470.90 | 222.2661 |
| 1790216963.05 | 35.1308 |
| 1790216784.99 | 4.1018 |
| 1790216646.75 | 50.6760 |
| 1790216105.40 | 90.0667 |
| 1790215920.28 | 6.0284 |
| 1790215612.84 | 12.5828 |

Those epochs fall between roughly **02:40Z and 03:19Z**, which overlaps the
two API deploys I made in that window (02:51Z `a9304db`, 03:08Z `e2f9999`)
and the database reads they triggered. **What can be established:**

- **no new `PROCESS_REPLACED`.** The only two remain the 135.191 s and the
  22.665 s already on the record, both hours old. The collector process was
  **not** replaced;
- the boot id did not change and `boots` is still 3, so it did not restart;
- collection did not stop: LADDER frames continue to 03:51:23Z and all
  twelve markets have frames inside the last four minutes.

**What is NOT established: the cause.** `GAP_LIVENESS_UNDETERMINED` says the
run could not determine whether its stream was live, which database
contention, a quiet venue or something else could each produce. The
coincidence in time with my deploys is recorded because it is suspicious,
**not** because it is demonstrated — and it must not be written up as
demonstrated later on the strength of this note. No remedial action was
taken and no counter was reset.

### 4 · obs-incentive-markets (run 1521, job 107485893307)

```
markets_receiving 12   markets_with_depth 12
frames_total 37201   frames_in_window 37201   frames_before_window 0
per-market newest: 03:48:24Z -> 03:51:31Z   (check 4: 01:46:07Z -> 01:47:55Z)
```

**No market has gone quiet.** All twelve carry depth; every frame is inside
the window and none precedes it.

### Coverage, recomputed with the unobserved head in the denominator

```
window opened            2026-09-23T04:00:00Z
first frame              2026-09-23T10:28:29Z
unobservable head        6.4747 h
newest frame             2026-09-24T03:51:31Z
span first->newest       17.3839 h
gap total                   745.8382 s = 0.207177 h
OBSERVED                 17.1767 h
elapsed since open       23.8586 h
```

**72.0% of elapsed, 71.6% of the full 24 h.** (Check 4: 70.1% / 63.7%.)

**This is NOT a complete-day observation and must never be reported as
one.** 6.47 h at the head were never observable because collection began at
10:28:29Z, and 745.8 s were lost to gaps — of which 581.4 s arrived in the
last hour, from a gap kind whose cause is not established.

### Verdict

Collection is running, continuous since 22:58:58Z under boot
`d61606169a1f410c`, with control true, the probe id unchanged, the allowance
barely touched and the deadline unmoved. No diagnosis was required under the
stop conditions — nothing disarmed, no budget exhausted, no run error — and
no remedial action was taken.

**THIS IS THE FINAL CHECK. No check 6 is armed.** The stop at
2026-09-24T04:00:00Z (`trig_01RKm5XcVvaUXCqLgbAnV2T6`) has not been moved.
After it fires the window is closed and the figure above — **17.1767 h
observed, 72.0% of elapsed, 71.6% of the calendar day** — stands as the
record.

---

# WINDOW CLOSED — 2026-09-24T04:00:00Z

The stop is the fixed window end. No extension, no replacement run, no
second window. Everything below is read from psql output in job logs.

## The stop, and who actually performed it

| | |
|---|---|
| stop write | run 1522, job 107487809007, **04:01:02Z** — `INSERT 0 1`, `bettor_live_observation = false` |
| independent re-read | run 1523, job 107488210009, **04:02:52Z** — `false` |
| **the run had already closed itself** | `RUN_CLOSE` rows went from 1 to **2**, the second stamped **2026-09-24 04:00:00+00** |

So the honest sequence is: **the collector observed its own window end and
closed at 04:00:00Z, 62 seconds before my write.** My stop was
belt-and-braces, not the cause. Recording this the other way round — a
control write that "stopped" a run which had already stopped — would credit
the wrong mechanism.

**The journal has stopped growing,** which is the test that matters. Two
readings of the same query, five minutes apart:

| read at | boot `d61606169a1f410c` rows | last row | gap rows |
|---|---|---|---|
| 04:02:58Z | 8,304 | 2026-09-24 04:00:00+00 | 17 |
| 04:08:07Z | **8,304** | **2026-09-24 04:00:00+00** | **17** |

A control flag reading false while rows keep arriving is not a stop. This
is a stop.

## Identities and the actual frame boundaries

| | |
|---|---|
| probe_id | `d5e9ae3d-257f-4948-a808-90d1bd3c5e48` — unchanged all window |
| boot_ids | **three**, never four |
| | `7435b23a98d049f3` 43 rows, 10:28:29Z → 10:28:59Z |
| | `8702807518fe44b4` 29,243 rows, 10:31:14Z → 22:58:35Z |
| | `d61606169a1f410c` 8,304 rows, 22:58:58Z → 04:00:00Z |
| FIRST persisted frame | **2026-09-23T10:28:29Z** |
| LAST persisted frame | **2026-09-24T03:59:56Z** (LADDER max `at`) |
| LADDER frames | **37,537**, all inside the window, **0** before it |

## Coverage against the FULL 24 h denominator

Denominator is the whole window `[2026-09-23T04:00:00Z,
2026-09-24T04:00:00Z)` with the pre-start gap counted **inside** it.

```
window                        24.0000 h
pre-start unobserved           6.4747 h   collector could not start until
                                          BETTOR_INCENTIVE_MANIFEST was set
                                          (~10:15Z); first frame 10:28:29Z
observed span first->last     17.5242 h
gaps inside the span           0.2072 h   (745.8382 s, 17 records)
tail after last frame          0.0011 h   (03:59:56Z -> 04:00:00Z)
-----------------------------------------
OBSERVED                      17.3170 h
UNOBSERVED                     6.6830 h
```

**72.2% of the 24-hour window. This is NOT a full-day observation and must
never be reported as one:** 6.4747 h of it were unobservable before the
collector could start, which is 27% of the window gone before the first
frame existed.

## Every gap, with from, to and reason

| reason | from (epoch) | to (epoch) | s |
|---|---|---|---|
| GAP_LIVENESS_UNDETERMINED | 1790217768.3829548 | 1790217928.9311712 | 160.5482 |
| GAP_LIVENESS_UNDETERMINED | 1790217470.9000216 | 1790217693.1660917 | 222.2661 |
| GAP_LIVENESS_UNDETERMINED | 1790216963.0491900 | 1790216998.1799746 | 35.1308 |
| GAP_LIVENESS_UNDETERMINED | 1790216784.9895895 | 1790216789.0914016 | 4.1018 |
| GAP_LIVENESS_UNDETERMINED | 1790216646.7474644 | 1790216697.4234276 | 50.6760 |
| GAP_LIVENESS_UNDETERMINED | 1790216105.3956854 | 1790216195.4624064 | 90.0667 |
| GAP_LIVENESS_UNDETERMINED | 1790215920.2842133 | 1790215926.3125865 | 6.0284 |
| GAP_LIVENESS_UNDETERMINED | 1790215612.8354895 | 1790215625.4182756 | 12.5828 |
| GAP_DISCONNECTED | 1790213690.2994990 | 1790213691.3002260 | 1.0007 |
| GAP_DISCONNECTED | 1790208626.4943056 | 1790208628.0049922 | 1.5107 |
| GAP_DISCONNECTED | 1790208179.2323046 | 1790208180.2508035 | 1.0185 |
| GAP_DISCONNECTED | 1790204337.5266368 | 1790204338.0454910 | 0.5189 |
| **PROCESS_REPLACED** | 1790204314.8349680 | 1790204337.5004090 | 22.6650 |
| GAP_DISCONNECTED | 1790173110.1466610 | 1790173111.6521392 | 1.5055 |
| GAP_DISCONNECTED | 1790159474.2896466 | 1790159474.7899637 | 0.5003 |
| **PROCESS_REPLACED** | 1790159339.0727410 | 1790159474.2642093 | 135.1910 |
| GAP_DISCONNECTED | 1790159308.9122818 | 1790159309.4390497 | 0.5268 |

**745.8382 s total.** Eight of the seventeen are
`GAP_LIVENESS_UNDETERMINED` between roughly 02:40Z and 03:19Z, totalling
581.4008 s — the last hour of the window, and overlapping two API deploys I
made at 02:51Z and 03:08Z. **Cause not established.** No new
`PROCESS_REPLACED` accompanied them, the boot id did not change and frames
kept arriving, so the collector was neither replaced nor restarted; beyond
that the coincidence is recorded as a coincidence and nothing more.

## Markets: receiving vs FULL DEPTH persisted

**12 receiving, 12 with depth persisted** — every market carries at least
one ladder side. Per-market depth *levels* reached, from the final read:

| market | frames | with_depth | max bid levels | max ask levels |
|---|---|---|---|---|
| ccpc-bilbrd-1album-any2026-alewar | 1,120 | 1,120 | 5 | 10 |
| ccpc-bilbrd-1album-any2026-benboo | 7,948 | 7,948 | 3 | 17 |
| ccpc-bilbrd-1album-any2026-beyonc | 1,344 | 1,344 | 7 | 8 |
| ccpc-bilbrd-1album-any2026-bileil | 3,525 | 3,525 | 4 | 22 |
| ccpc-bilbrd-1album-any2026-charoa | 1,261 | 1,261 | 4 | 9 |
| ccpc-bilbrd-1album-any2026-chaxcx | 846 | 846 | 2 | 7 |
| ccpc-bilbrd-1album-any2026-coldpl | 6,018 | 6,018 | 3 | 20 |
| ccpc-bilbrd-1album-any2026-doechi | 1,941 | 1,941 | 5 | 19 |
| ccpc-bilbrd-1album-any2026-dualip | 2,374 | 2,374 | 5 | 17 |
| ccpc-bilbrd-1album-any2026-eminem | 5,716 | 5,716 | 9 | 18 |
| ccpc-bilbrd-1album-any2026-fraoce | 3,603 | 3,603 | 5 | 23 |
| ccpc-bilbrd-1album-any2026-jusbie | 1,515 | 1,515 | 2 | 19 |

(Counts from the 04:06:13Z read; they total 37,537.) **These twelve markets
are ONE programme and ONE event, not twelve independent observations.**
Whether a ladder was ever *complete* — every level the venue held, not
merely a non-empty side — is **UNKNOWN**: the journal records the levels we
received and cannot say what was withheld. Reported as unknown, not as
full.

## Allowance consumed, against its ceilings

| resource | used | ceiling |
|---|---|---|
| HTTP (manifest 0 + recheck 2 + retry 0) | **2** | 8 |
| socket connects | **7** | 20 |
| socket subscribes | **14** | 40 |
| `general_max_distinct` | **0** | — untouched, as required |
| boots | 3 | — |
| deadline_at | 2026-09-24T04:35:48+00:00 | never moved |

Final control read: **`false`**.

## The opportunity script

`python research/beta48/bettor_incentive_opportunity.py` requires a mode;
both were run.

- `--self-test` → `self-test OK -- terms from the captured manifest, 12
  markets, pool $50, DF 0.25, target 500`.
- `--scenario` → wrote
  `research/beta48/acceptance/incentive_opportunity.json`. Terms as
  captured: `culture_low_20260921`, pool $50, DF 0.25, target 500.

**The scenario's own header states the limit and it is repeated here: the
ladders it runs on come from markets carrying NO incentive programme, so
its `gross$` column is NOT a measurement of what they would have paid —
there was no pool on them.** The figures show the pipeline computes on real
depth. Nothing has been earned; no money has moved; measured competing
depth is not reward share, and a scenario is not a measurement.

## What this window is, and is not

It **is** 17.3170 h of persisted order-book depth across twelve markets of
one programme and one event, with every gap enumerated, the allowance
barely touched and the stop verified from durable evidence.

It is **not** a full day, **not** twelve independent observations, **not**
evidence about trading performance, and **not** earned money. No orders
were placed, no trading control was changed, no credential moved, nothing
was re-armed and no counter was reset.

**The window is closed. This figure stands as the record.**

## Postscript — a counter that went down

Two readings of `bettor_incentive_run`, from the same query:

| read at | `run_reconnects` | `run_resubscribes` |
|---|---|---|
| 03:51:15Z (check 5) | 4 | 8 |
| 04:08:05Z (after close) | **2** | **4** |

**A monotone counter does not decrease, so these are not cumulative.** The
most likely reading is that the row carries the CURRENT run's tally and was
rewritten when the run closed at 04:00:00Z — but that is inference, and I
have not read the writer that sets it.

I am recording it because the check-5 section above quotes the 03:51Z pair,
and a reader comparing the two sections would otherwise find a
contradiction with nothing against it.

It changes nothing in the coverage figure: reconnects and resubscribes are
not inputs to it, the gap records are, and those were identical in both
readings (17 rows, 745.8382 s). **Neither value should be cited as a
window-total for reconnections until the field's semantics are read from
the writer.**
