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

## 2026-09-23 19:36Z — RN1X integration deployed and verified from persisted rows

Authorization: "complete the integration and deploy the functioning
shadow systems… Report completion only from persisted end-to-end
evidence and the published interface."

### The migration question, settled from the catalog

`100_rn1_seeded_experiment.sql` applied **19:02:46Z**. My earlier readback
reported `rn1x_tables = 0` at 19:02:19Z — twenty-seven seconds before the
`6c9a9fb` deploy finished at 19:02:51Z. The tables were absent because the
build had not landed, not because the migration failed. I had been about
to diagnose a migration failure that never happened.

### Persisted evidence

| At (UTC) | positions | decisions | orders | fills | outcomes | cursor |
|---|---|---|---|---|---|---|
| 19:23:28 | 0 | 0 | 0 | 0 | 0 | — (control row absent) |
| 19:28:47 | 19 | 1,158 | 40 | 4 | 19 | 41 |
| 19:35:58 | — | 1,706 | — | — | — | 145 |

Decisions by operating state at 19:35:58Z:
`ORDER_WORKING 1300`, `NO_RESIDUAL 271`, `HOLD_NO_FEASIBLE_PAIR 135`.

`reconciles = true` on the written positions. Both writer locks held
(`desk_lock_free = f`, `rn1x_lock_free = f`) — the rn1x loop is the single
writer on its own key, not a standby of the desk's.

### The fail-closed sequence, observed rather than asserted

- 19:21:31Z heartbeat: `{"ran": false, "why": "CONTROL_ROW_ABSENT",
  "state": "STOPPED"}` — deployed, holding its lock, writing nothing.
- 19:24:36Z `rn1x-on` → `rn1x_shadow = true`.
- 19:28:26Z heartbeat: `REPLAYED`, wrote a position AND refused another
  for `initial flat inventory/history completeness not verified`.

Registration did not start it; a database write did.

### Containment, unchanged throughout

`acct_fc2d773a2afa4851` still `paused = t`, `ACCOUNTING_UNCERTAIN`, and
the last desk decision is still **16:37:30Z**. Nothing in this work reads
or writes the desk's tables.

### Blockers, still named and still not worked around

- `SECOND_HALF_UNDEFINED` — no event-progress feed, so the loss exit is
  unavailable on every position and only the PAIRING half of the policy
  is under test. Missing dependency: a per-sport event clock with observed
  period boundaries, joined point-in-time.
- `NO_CONTEMPORANEOUS_BOOK` — no archived bid/ask/depth for these
  instants. Tape prints are executions by others, not a standing book.
- `FEED_POSTDATES_SETTLEMENT` — 15.07 h detection lag on the measured
  seed, postdating settlement by 11.22 h. No forward lane can be seeded
  from this feed, so the experiment is HISTORICAL_REPLAY by necessity.

### Deployment constraint recorded, not routed around

Both `sportsassets-api` and `sportsassets-workers` track the same
auto-deploy branch, so registering the loop in `workers/all.py` would need
a push that restarts the observation collector mid-window. Both loops are
hosted in the API lifespan instead and released by commit id; the frontend
went to the Netlify branch with `[skip render]`.
