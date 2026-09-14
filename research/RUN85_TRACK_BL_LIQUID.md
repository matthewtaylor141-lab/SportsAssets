# RUN 85 TRACK B-L — LIQUID-COHORT EXPERIMENT

**A separate experiment. Exploratory decision evidence. Never pooled with
Track A, which stays frozen, unmodified and running.**

Question: *under what observable PMUS market conditions, if any, is a passive
maker/maker strategy reachable — and with enough capacity to justify a tiny
live execution test?*

---

## TERMINOLOGY — CORRECTED AND LOCKED

A touch is an optimistic **proxy**, never a fill. The following are the only
permitted spellings:

```
60S_TOUCH_UPPER_BOUND_FREQUENCY   1 / 299 = 0.33%
BOTH_LEGS_TOUCHED_WITHIN_60S      0 / 299

MAKER_FILL_PROBABILITY            NOT_IDENTIFIED
PAIR_COMPLETION_PROBABILITY       NOT_IDENTIFIED
```

My earlier report said `FIRST_LEG_FILL_PROBABILITY (F3) = 1/299`. That was
wrong — it converted a touch upper bound into a fill probability, which is
exactly the promotion this whole study forbids. Retracted.

## TRACK A FINDING — PRESERVED VERBATIM

```
TRACK_A_SEGMENT_2_ADMITTED     = YES
TRACK_A_PRIMARY_OBSERVATIONS   = 1525
TRACK_A_POSTABLE_PAIRS         = 299

5/10/30/60 s
  LONG_TOUCHES                 = 0 / 0 / 0 / 1
  SHORT_TOUCHES                = 0 / 0 / 0 / 0
  BOTH_TOUCHES                 = 0 / 0 / 0 / 0

60S_TOUCH_UPPER_BOUND_FREQUENCY = 1 / 299
MAKER_FILL_PROBABILITY          = NOT_IDENTIFIED
PAIR_COMPLETION_PROBABILITY     = NOT_IDENTIFIED

TRACK_A_COHORT_OBSERVABLE       proof 11/12 · segment 2 7/12

TRACK_A_48H_CLASSIFICATION =
    C — DISPLAYED EDGE ONLY / EXECUTION NOT ESTABLISHED
```

Non-touch is **not** negative expectancy. It is absence of execution evidence.

---

## THE CHANGE THAT MATTERS — RESTING HORIZONS TO 60 MINUTES

Track A's 5/10/30/60 s horizons cannot answer whether a real passive order
fills, because no real passive strategy cancels after a minute. B-L observes a
hypothetical order resting for up to an hour:

```
5s  10s  30s  60s  5m  15m  30m  60m
```

### How that is afforded without raising the rate

A **300-second cycle** with a five-read burst per market per cycle at offsets
`{0, 5, 10, 30, 60} s`. Because the cycle is 300 s and the long horizons are
exact multiples of it, **the long horizons cost nothing extra** — the 5 m
observation of cycle *c* IS the t0 read of cycle *c+1*, the 15 m is *c+3*, the
30 m *c+6*, the 60 m *c+12*. Sparse scheduled observation, not polling.

Market lanes are staggered so no two reads ever want the same instant. The
offsets differ by `{5,10,20,25,30,50,55,60}`, so a stagger is admissible only
if it avoids that set; a search over the 2.5 s grid gives 16 admissible lanes,
of which the first six are used:

```
lane staggers (s):  0.0  2.5  15.0  17.5  80.0  82.5
```

### Rate, and why six lanes

```
6 markets x 5 reads / 300 s          = 0.1000 rps
Track A worst case (grid density)    = 0.2860 rps
                                       ------
aggregate worst case                 = 0.3860 rps   < 0.4000 nominal
```

Six lanes is the largest count that stays under the ceiling **even if Track A
is simultaneously running at its design maximum**. `RATE_INCREASE = NO` holds
against the sum, not just against each job. The minimum spacing inside a burst
is 2.5 s, the established floor.

B-L reads `/book` only. The paired `/bbo` is dropped because B-1 is settled and
the ladder already carries near-touch size — that halves the request cost and
buys the long horizons. Stated as a deliberate difference from Track A, not an
oversight.

---

## SELECTION — OBSERVABLE AT SELECTION TIME ONLY

Discover open sports markets, then rank on evidence visible at t0: open and
observable · both sides present · game-level · economically live · price not
only in the extreme tail · tight spread · meaningful near-touch size · spread
across independent events and sports.

**Spread/mid is diagnostic, not the objective.** Segment 2 showed four of seven
survivors quoting 50–100% of mid, which explains non-touch — but B-L must learn
whether tighter books *trade* differently, not assume it. Every candidate
records, independently: `bid ask mid spread spread_ticks spread_over_mid
near_touch_bid_qty near_touch_ask_qty state time_to_start sport league`.

Selection never uses subsequent profitability. The cohort is frozen per block.

---

## ORDER MODEL — FROZEN PRICES, NO CHASING

At t0 rest `LONG at bestBid(t0)` and `SHORT at 1 - bestAsk(t0)`. Those prices
are **frozen for the life of that hypothetical order**. The base experiment
does not reprice and does not chase. Each leg is classified independently at
every horizon as `NOT_TOUCHED / TOUCHED / CROSSED` against its own original
resting price.

## SEQUENTIAL PAIR COMPLETION — THE CENTRAL QUESTION

Simultaneous touch is **not** a pair definition. For each modelled first-leg
execution, freeze the side, price, quantity and modelled fill time; then ask
whether the **original** opposite order plausibly executes *afterwards*, and
report `P_SECOND_LEG_PROXY | FIRST_LEG_PROXY` separately for F0/F1/F2/F3 at
5s/10s/30s/60s/5m/15m/30m/60m from the first fill.

## RESIDUAL RISK

For every one-leg-only execution, residual markout measured **from the first
modelled fill time**, at every observable horizon. Favourable and adverse both
retained. Report mean, median, p25, p10, worst observed, and intervals only
where the sample supports them. Residual loss is never buried inside completed
pairs.

## WATERFALL

```
completed pair:   DISPLAYED_SPREAD_CAPTURE
                + rounded LONG maker rebate
                + rounded SHORT maker rebate
                = PRE_ADVERSE_SELECTION_PAIR_BUDGET

incomplete:       first-leg economics + first-leg rebate + residual markout
                  (the second-leg rebate is NOT granted unless that leg is
                   modelled executed)
```

## STRATIFY, DO NOT OPTIMISE

Preregistered descriptive groups only: price (tail / moderate / near-mid) ·
spread (1 tick / 2-3 / 4+) · state (pregame / live) · sport · near-touch queue
(low / medium / high). A subgroup that looks good after the fact is a
**candidate for a forward holdout**, not a strategy.

---

## OPERATIONAL NOTE ON TRACK A — A COVERAGE LOSS WORTH DECIDING ABOUT

The 00:00Z scheduled segment **never fired**. Run #4 was itself 2h31m late
(20:30:42Z instead of 18:00Z) and was still capturing at 00:00Z, so the
concurrency group — which exists to stop two captures sharing the rate budget —
dropped the pending run rather than queueing it.

That is self-perpetuating: a late segment eats the next one.

```
16:00Z -> 20:30Z   gap (window open, no capture)
20:30Z -> 01:30Z   segment 2, captured
01:30Z -> 06:00Z   gap (00:00Z segment dropped)

realised coverage since window open  ~5h of 10.9h  =  46%
nominal                                              83.33%
```

Track A timing is **locked and I have not touched it**. Recording the gap
exactly, imputing nothing, and not raising the rate to compensate. The decision
of whether to accept ~46% coverage or change the schedule is the owner's.

---

## RATE GATE — WHAT CAN AND CANNOT BE PROVEN

`B_L_AGGREGATE_RATE_SAFE = NOT_VERIFIED`. The earlier argument proved an
average-rate budget and called it spacing safety. Those are different claims:
two independently paced streams can collide no matter how each paces itself,
because neither stream's spacing bounds the combined minimum gap.

### Option A — shared cross-workflow pacing: INFEASIBLE

Two Actions runners are separate machines with no shared clock or lock. Any
coordination channel available here (a repo file, the GitHub API) has latency
and contention measured in seconds, which is the same order as the 2.5 s floor
it would be trying to enforce. It cannot operate on actual request start times.

### Option B — poll the API and pause: HAS AN UNBOUNDED BLIND WINDOW

A pre-flight "is Track A running or queued?" check cannot see a *delayed*
scheduled run, because GitHub creates the run record only when the run actually
starts. Run #4 is the proof:

```
cron boundary        2026-09-13T18:00:00Z
run record created   2026-09-13T20:30:42Z    (+2h30m42s)
run started          2026-09-13T20:30:42Z    (created == started)
```

For 2h30m that run did not exist in the API. Track A can therefore appear at
any moment inside a B-L block, and the blind window is unbounded because cron
lateness is unbounded. Option B degrades to best effort.

### Option C — the only hard guarantee available

GitHub's **concurrency group** is platform-level mutual exclusion: two runs in
the same group never execute simultaneously. That is a guarantee about actual
execution, not about planned schedules, and it is the only mechanism here that
survives cron nondeterminism.

Its cost is real and must be stated rather than buried: if a Track A segment
becomes due while a B-L block is running, Track A **waits** for it. A Track A
start could therefore be delayed by up to one B-L block length.

It would not cause a Track A *drop*. A drop requires two runs pending in the
group at once; Track A's crons are six hours apart and a B-L block is minutes,
so two Track A runs can never be pending because of B-L.

This changes Track A's realized start times, which is why it is an owner
decision and not mine to take.

---

## BLOCK_1 — PERMANENT RECORD, NOT TO BE REHABILITATED

```
B_L_BLOCK_1_STATUS      = FAILED_DISCOVERY
SCIENTIFIC_OBSERVATIONS = 0
ECONOMICALLY_USABLE     = NO
sealed at               research/evidence/trackbl/run85_trackbl_BLOCK_1_20260914T033223Z
```

12 pages from offset 0, 1,200 events, **0 candidates**, no capture. The workflow
reported success, which was itself wrong.

**Cause.** `/v1/events` is id-ascending, so offset 0 returns the OLDEST events.
Selection was built from the wrong end of the list. This is the same error
Phase 2C made and that I documented and retracted earlier in this run; I
reintroduced it.

The filter was NOT at fault, and that is established rather than assumed: run
against the sealed 2G-R payload as a control it admits 10,471 of 51,566 market
rows (~20%), rejecting PROP 19,399, FUTURE 15,623, no-quote 5,666, closed 403.

**The diagnosis is weaker than it should be**, because the discovery bodies were
stripped from the log. `PAGINATION_ERROR` versus `PARSER_SHAPE_ERROR` cannot be
separated from BLOCK_1's own evidence. That is fixed for BLOCK_2, not for
BLOCK_1 — a failed block is preserved as it was.

## REPAIR SCOPE APPLIED (BLOCK_2 ONWARD)

1. **Diagnostics retained.** Per page: path, params, offset, HTTP status,
   response bytes, the venue's `response_sha256` AND an independently
   recomputed body digest, event count, first/last event id, the full id list
   and the top-level keys — plus a bounded raw sample (50 head + 50 tail events
   per page, verbatim). Sealed **whether or not the block succeeds**; a test
   pins that the diagnostics are written before the exit path.
2. **Current-end pagination.** Direction is verified, not assumed; the current
   end is located by geometric probing; the frame is walked backwards from it.
   `page=` and `skip=` — disproved in Phase 2E — are pinned as never reappearing.
3. **Hard failure.** `candidate_count < REQUIRED_BLOCK_SIZE (4)` seals
   diagnostics, sets `BLOCK_STATUS = FAILED_BLOCK_TOO_SMALL`, refuses to
   capture, and **exits non-zero**. Seal failure exits 3. No failed block may
   read as success or advance numbering as though data were collected.
4. **Nothing else changed.** Track A, selection criteria, economic definitions,
   horizons, rate policy, shared concurrency group, 20-minute cap and the
   no-cross-block-state rule are all untouched. Filters were **not** loosened to
   reach six markets.

If BLOCK_2 also returns zero candidates, the retained bodies decide between
pagination, parser/schema, admission logic and a genuinely unsuitable universe
**before** any BLOCK_3.
