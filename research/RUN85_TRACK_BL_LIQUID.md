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

---

## BLOCK_2 — PERMANENT RECORD, NOT TO BE REHABILITATED

Dispatched under commit `b539864`, exactly as approved. Run `34839559613`, job
`103961105306`, 2026-09-14T11:42:29Z → 11:43:45Z. Seven files sealed,
`SEAL_VERIFIED = True`, runner exit 2, workflow conclusion **failure**.

```
B_L_BLOCK_2_STATUS      = FAILED_DISCOVERY_BOUND_NOT_ESTABLISHED
SCIENTIFIC_OBSERVATIONS = 0
ECONOMICALLY_USABLE     = NO
```

### Discovery, reported under the terminal-boundary rule

```
GEOMETRIC_PROBES            2000  4000  8000  16000  32000  64000
                            every one returned a FULL page of 100 events

    offset  2000   events=100   first=2002    last=2101
    offset  4000   events=100   first=4002    last=4101
    offset  8000   events=100   first=8004    last=8103
    offset 16000   events=100   first=16349   last=16483
    offset 32000   events=100   first=51389   last=51488
    offset 64000   events=100   first=83420   last=83519

TERMINAL_BOUNDARY_EVIDENCE  NONE. No empty page was ever returned, and no
                            other venue signal of a list end was observed.
LAST_NONEMPTY_OFFSET        64000  (the configured search ceiling)
FIRST_TERMINAL_OFFSET       NOT_OBSERVED
CURRENT_END_LOCATED         NO
```

**The ceiling is not the end.** `END_PROBE_STEPS` stops at 64000, and 64000
returned a full page, so the walk ran out of configured search before it ran out
of list. The id/offset relation is also nonlinear and widening — offset 32000
returns ids from 51389, offset 64000 ids from 83420 — so the list demonstrably
extends past the ceiling by an unknown amount.

The runner printed `CURRENT_END_LOCATED = BOUNDED_BY_PROBE` and
`BLOCK_STATUS = FAILED_BLOCK_TOO_SMALL`. Under the rule the owner issued after
dispatch, that spelling is wrong in one direction and incomplete in the other:
`BOUNDED_BY_PROBE` is **NO**, and the governing failure is the unestablished
bound, not the empty block — the zero-candidate count is downstream of a frame
taken from a place that was never shown to be the current end. The sealed
evidence keeps the words the runner actually wrote; this reclassification sits
beside it and does not overwrite it. Nothing about the block is rehabilitated by
either spelling.

### What the frame contained

```
FRAME                       offsets 64000 → 62700, 14 pages, 100 events each
discovery_events            1400  (0 shared ids between adjacent pages)
DISCOVERY_MARKET_ROWS       8532
candidates                  0
BLOCK_SIZE                  0   (REQUIRED_BLOCK_SIZE 4)

PAGINATION_DIRECTION        VERIFIED_ASCENDING
OVERLAP_CHECK               0 shared ids
NEWER_THAN_PRIOR_PAGE       YES
SELECTION_RULE_UNCHANGED    YES  (BL-SELECT-1, filters NOT loosened)
VENUE_REQUESTS              22   (2 anchor + 6 probes + 14 frame)
HTTP_429                    None
RATE_GATE                   PASS
TRACK_A_OVERLAP             IMPOSSIBLE_BY_SHARED_CONCURRENCY
```

The hard-failure repair worked as specified: no capture was attempted, the
runner exited non-zero, and the workflow reported failure rather than success.

### A SECOND SELF-INFLICTED DEFECT, FOUND HERE

The non-zero exit failed the capture step, and **the commit step had no
`if: always()`**, so GitHub skipped it. The seven sealed files (2,712,334 bytes)
therefore landed only as Actions artifact `10345835689` — which the analyst
container cannot reach, because its egress denies the Actions blob store. The
repair scope said a failed block must seal its evidence; it did seal it, and
then the workflow stranded it in the one place the evidence was needed from and
could not be read.

That is now fixed (`if: always() && env.RUN85_BL_OUT != ''`), and a directory
courier — `run85-courier-dir.yml` — carries the stranded bytes into the repo so
the cause can be classified from evidence rather than from inference. The
courier verifies every file against the driver's own `checksums.sha256` before
the copy and again after it. A failed block stays failed: couriering its
evidence makes the failure readable, nothing more.

### CAUSE CLASSIFICATION — OPEN

`PAGINATION / PARSER-SCHEMA / ADMISSION / CURRENT_UNIVERSE` cannot be decided
from the job log alone. What the log does settle:

- **Not a gross parser failure at the event→market level.** 8,532 market rows
  were parsed out of 1,400 events, so `markets` was found and walked. A schema
  difference in the *fields the filter reads* (`sportsMarketTypeV2`,
  `bestBidQuote`, `bestAskQuote`, `active/closed/archived`) would look exactly
  like this and is still open.
- **Not a rate or transport failure.** 22 requests, no 429, no error status.
- **PAGINATION is not excluded** — the frame's position in the list is
  unestablished, which is the block's governing failure.

The runner records no per-reason rejection counts, so the decision needs the
sealed body samples. **No filter changes, and no BLOCK_3, until the cause is
established.**

---

## BLOCK_2 — CAUSE, CLASSIFIED FROM THE SEALED BYTES

The diagnostics were couriered in (`0c3b4b3`) and re-verified: all six files
`sha256sum -c` OK against the driver's own `checksums.sha256`. Because each page
holds 100 events and the sample keeps head 50 + tail 50, **the sample is the
complete page** — all 1,400 frame events and all 8,532 market rows were
re-derived here, matching the runner's counts exactly. The runner's own
admission test was imported, not retyped.

```
CAUSE = PAGINATION   (specifically: the discovery QUERY, not the walk direction)
```

### The decisive evidence

```
B-L discovery call      GET /v1/events  {"limit": 100, "offset": N}
Phase 2B / 2G-R call    GET /v1/events  {"active": "true", "closed": "false", "limit": 100}
```

B-L dropped the venue-side filters. Without them `/v1/events` is the venue's
**entire historical archive**, id-ascending:

```
offset     0    startDate span  2025-10-31 .. 2025-11-18
offset 62700..64000              2026-08-15 .. 2026-08-28
today                            2026-09-14
```

The 64000 ceiling landed roughly two and a half weeks short of the present, in
an archive stretching back ten and a half months. The frame is therefore a frame
of finished games:

```
event closed              1400 / 1400  true
market status             8532 / 8532  MARKET_STATUS_RESOLVED
market closed             8532 / 8532  true
admission rejection       8532 / 8532  at test 1 (closed / archived / inactive)
```

Every single row died on the first test. Nothing ever reached the market-type,
quote or spread tests.

### The other three candidate causes, each excluded on evidence

- **ADMISSION — EXCLUDED.** The filter never got a chance: 8,532 of 8,532 rows
  were rejected as closed before any economic test ran. The type census also
  shows the frame is full of the types the filter *wants* — 1,247 MONEYLINE,
  760 SPREAD, 739 TOTAL, 435 DRAWABLE_OUTCOME — so the admitted-type set is not
  the constraint. Filters were not loosened and do not need to be.
- **CURRENT_UNIVERSE — EXCLUDED.** The live universe was never reached, so it
  was never tested. Under the filtered query the venue served 1,066 quoted rows
  of 1,579 with `MARKET_STATUS_OPEN` at offset 0 (2G-R, sealed). There is no
  evidence the current universe is unsuitable.
- **PARSER/SCHEMA — NOT THE CAUSE, BUT IT CONCEALS A REAL DEFECT.** See below.

### The missing quotes are a consequence, not a defect

`bestBidQuote` / `bestAskQuote` are absent on **0 of 8,532** frame rows and 0 of
100 at offset 0 — a resolved market carries no quote, only `outcomePrices`
(`["0","1"]`) and `marketSides[].price`. Under the filtered query the same
fields are present and populated (`{"value": "0.3810", "currency": "USD"}`,
1,066 of 1,579 rows in the sealed 2G-R payload). The filter reads the right
fields; it was handed the wrong slice of the list.

### A GENUINE LATENT DEFECT, FOUND AND NOT YET FIXED

`primaryTag` is a **dict** on the venue's real events —
`{"id": "484", "label": "ITF Mens", "league": …}` — in the B-L payload and in
the sealed 2G-R payload alike. `candidates()` assigns it straight to `sport`,
and `select_block()` uses `sport` as a dict key. Reproduced against a real
sealed venue event with an open quoted market spliced in (the only change):

```
candidates admitted   1
sport field type      dict
select_block RAISES   TypeError: unhashable type: 'dict'
```

**A successful discovery would have crashed the runner.** Both blocks returned
zero candidates, so this never fired — one defect masked the other. My own test
missed it because its fixture used `"primaryTag": "nfl"`, a string the venue
does not send; a fixture that does not match the venue's shape cannot pin the
venue's shape.

### STATE

```
B_L_BLOCK_2_CAUSE            = PAGINATION (dropped active/closed query filters)
B_L_BLOCK_2_SECOND_DEFECT    = PARSER/SCHEMA, primaryTag is an object not a string
FILTERS_CHANGED              = NO
BLOCK_3_DISPATCHED           = NO
```

Both defects are mine, both are now named from evidence, and neither is fixed
yet — the repair scope is the owner's to set before any BLOCK_3.

---

## REPAIR APPLIED FOR BLOCK_3 (owner-authorized, engineering only)

### FIX 1 — the current-universe query

```
before   GET /v1/events {"limit": 100, "offset": N}
after    GET /v1/events {"active": "true", "closed": "false",
                         "limit": 100, "offset": N}
```

The archive crawl is **gone, not merely unused**: `END_PROBE_STEPS` and
`FRAME_PAGES_BACK` are deleted and a test asserts the identifiers are absent
from the module. Discovery now walks offsets forward from 0 and stops at the
first empty page — a terminal boundary that is genuinely reachable, because
the filtered list is the current universe. 2G-R's sealed log shows that list
is ~1,900 events over 19 pages; the walk is bounded at 26.

**And the query is not taken on trust.** The returned frame is re-counted from
the payload, and the block refuses to capture if the venue contradicts the
scope it was asked for:

```
EVENTS_RETURNED · EVENTS_WITH_CLOSED_TRUE · MARKETS_TOTAL
MARKETS_OPEN · MARKETS_RESOLVED · MARKETS_WITH_BID_AND_ASK

EVENTS_WITH_CLOSED_TRUE > 0  ->  FAILED_QUERY_SCOPE_NOT_HONOURED, exit non-zero
MARKETS_OPEN == 0            ->  FAILED_NO_OPEN_MARKET_ROWS,      exit non-zero
```

### FIX 2 — primaryTag normalized, never keyed on

`normalize_tag()` reads the venue's object **field by field**:

```
PRIMARY_TAG_SHAPE   OBJECT | STRING | NULL | UNKNOWN
PRIMARY_TAG_ID      raw["id"]
PRIMARY_TAG_LABEL   raw["label"]
PRIMARY_TAG_LEAGUE  raw["league"]["slug"]  (else league name)
PRIMARY_TAG_SPORT_ID raw["league"]["sportId"]

SPORT_KEY   "sportId:<n>" from the venue's own numeric sport id, else NOT_IDENTIFIED
LEAGUE_KEY  the league slug, else the tag slug, else the tag id, else NOT_IDENTIFIED
```

The object is never serialized and called a sport, and there is no text search
over it — a test reads the function's own source to pin that. An unrecognised
shape is classified `NOT_IDENTIFIED` and **carried**, not dropped and not
crashed on; a bare string is supported as a documented compatibility case the
venue has not been observed to send. `tags` are reduced to explicit slugs by
name for the same reason.

### Tests — 36, from real sealed venue shapes

`research/run85_trackbl_venue_shapes.json` holds primaryTag objects and an
open, quoted, admitted-type market row lifted verbatim from the BLOCK_2
discovery samples and the 2G-R request log. The old string-only fixture is
exactly why the defect survived, so the pins are now the venue's own bytes.

```
self-runner   36 passed, 0 failed
pytest        36 passed, 0 failed
whole gate    141 passed (5 suites, pytest)
```

Object / string / null / unknown tags · admission with an object tag ·
`select_block` does not raise on real object tags · the query filters are
present · `page=`/`skip=` still absent · offset is the only pagination ·
the scope census · a bounded walk that never enumerates the archive ·
BLOCK_TOO_SMALL still exits non-zero · diagnostics still seal on failure.

### Not changed

Admission logic, allowed market types, quote requirements, spread rules,
price-band rules, block-size requirement, rate policy, concurrency
architecture, the 20-minute cap, supported horizons, the no-cross-block-state
rule, economic formulas, Track A. `SELECTION_RULE_VERSION` stays `BL-SELECT-1`
because the selection *rule* is untouched; only the payload it is fed and the
way a tag is read have been repaired.

---

## BLOCK_3 — FACTUAL RECORD

Observed facts only. No interpretation in this section.

```
BLOCK_STATUS                 OK
ECONOMICALLY_USABLE          YES
SCIENTIFIC_OBSERVATIONS      120
SELECTED_MARKETS             6
CAPTURE_ELAPSED_S            1042
CHECKSUMS                    7/7 PASS
COMMIT                       4734e95
COHORT_SHA256                82632fa9ba549c8e1f204e22f8c25efd368d3001bfc6ca091b7d1ececae97ceb
EVIDENCE                     research/evidence/trackbl/run85_trackbl_BLOCK_3_20260914T171339Z
```

### Discovery scope

```
QUERY_FILTERS                {"active": "true", "closed": "false"}
EVENTS_DISCOVERED            2600     (26 pages x 100, offset walk from 0)
EVENTS_WITH_CLOSED_TRUE      0
MARKET_ROWS                  77020
OPEN_MARKET_ROWS             76869
RESOLVED_MARKET_ROWS         142
MARKETS_WITH_BID_AND_ASK     45797
CANDIDATES                   20403
DISTINCT_EVENTS              726

DISCOVERY_LIST_EXHAUSTED     NO
FIRST_TERMINAL_OFFSET        NOT_OBSERVED
DISCOVERY_FRAME_SCOPE        BOUNDED_PREFIX_OF_CURRENT_ACTIVE_UNIVERSE
```

The walk reached its configured 26-page bound without an empty page. **This is
not the whole current PMUS market universe** and must never be described as
one: it is a bounded prefix of the current active list, and selection ran only
over that prefix.

### Tag shapes, as returned

```
PRIMARY_TAG_OBJECT_COUNT     1322
PRIMARY_TAG_STRING_COUNT     0
PRIMARY_TAG_NULL_COUNT       1278
PRIMARY_TAG_UNKNOWN_COUNT    0
```

### Cohort

```
COHORT_COMPOSITION           5 TAIL · 1 MODERATE · 0 NEAR_MID

asc-bun-bre-aug-2026-09-19-fh-pos-2pt5    bun      b=0.9800 a=0.9900  TAIL
asc-cfb-clmsn-cah-2026-09-25-pos-17pt5    cfb      b=0.9850 a=0.9900  TAIL
aec-ufc-gabste-seasha-2026-09-19          (null)   b=0.9000 a=0.9100  TAIL
aec-atp-jjwol-lucamb-2026-09-14           atp      b=0.9200 a=0.9300  TAIL
asc-mlb-sd-col-2026-09-15-pos-2pt5        mlb      b=0.9850 a=0.9900  TAIL
aec-t20icr-eng-slr-2026-09-15             t20icr   b=0.7800 a=0.7900  MODERATE

SPORTS        sportId:2, 3, 5, 8, 9 and one NOT_IDENTIFIED (null primaryTag)
SELECTION_RULE_VERSION       BL-SELECT-1
SELECTION_RULE_UNCHANGED     YES
BLOCK_FROZEN                 YES
REQUIRED_BLOCK_SIZE          4
SELECTED_BLOCK_SIZE          6
```

### Capture and rate

```
capture rows                 120 / 120, all HTTP 200, all carrying a book
lanes x cycles x offsets     6 x 4 x {0,5,10,30,60} s
VENUE_REQUESTS               146   (26 discovery + 120 capture)
PLANNED_MIN_GAP_S            2.5
observed min inter-read gap  2.5001 s
ANY_GAP_LT_2_5S              0
HTTP_429                     0
RATE_GATE                    PASS
SHARED_CONCURRENCY_GROUP     YES
TRACK_A_OVERLAP              IMPOSSIBLE_BY_SHARED_CONCURRENCY
```

Track A segment #6 held the lock until 17:13:25Z; BLOCK_3 started at that
instant and was never cancelled.

### Clock

```
MAX_SLOT_TIMING_ERROR_S      3.46
HORIZON_ANALYSIS_CLOCK       ACTUAL_OBSERVED_TIMESTAMPS
```

Nominal schedule times are never substituted for actual observation times.
Every horizon is evaluated on measured elapsed time, and each reported horizon
carries its `TARGET_HORIZON`, `ACTUAL_ELAPSED` and `HORIZON_ERROR`.

---

## BLOCK_3 — ECONOMIC READ

Analyzer `research/run85_trackbl_block3_economics.py`, read-only over the
committed bytes. Full output sealed at
`research/evidence/trackbl/run85_trackbl_BLOCK_3_economics.txt`. Nothing in the
runner, selection, cohort, rate policy or Track A was touched.

### The order model and the short-leg algebra

One book, quoted in the LONG token's price space (FINDING B-1: `longQuote ==
bestAsk`, `shortQuote == 1 - bestBid`, 110/110). A SHORT at price *p* is a SELL
of the long token at 1 − *p*. So, frozen at each t0:

```
LONG  maker price = b0                 a resting BUY  of the long token
SHORT maker price = 1 - a0             a resting SELL of the long token at a0
PAIR_COST            = b0 + (1 - a0) = 1 - (a0 - b0)
PAIR_DISPLAYED_GROSS = a0 - b0
```

Both touch tests therefore live in the ladder's own price space:

```
LONG_TOUCH(h)   <=>  best_ask(h) <= b0
SHORT_TOUCH(h)  <=>  best_bid(h) >= a0
```

The short test compares to **a0**, never to `1 - a0` and never to `shortQuote`.
Comparing the short-token spelling against the long-token ladder is the sign
error this section exists to rule out.

### 24 hypothetical pairs · 2 excluded as degenerate

24 pairs (6 markets × 4 cycle-t0s), all quoted two-sided, all 1-tick spreads.
Two later-cycle t0s on `asc-bun-…-fh-pos-2pt5` opened on a **collapsed bid**
(b0 = 0.0200 against a0 = 0.9900). Those are marked `DEGENERATE_BOOK` and are
excluded from every headline figure — their 0.97 "displayed gross" is a broken
book, not an opportunity.

### Horizon clock and eligibility

Eligibility is two-sided: a cycle counts for H only if it **reaches** H *and*
has an observation **inside** the window. Getting that wrong inflates the
denominator with cycles that never had a read in the window, which makes
"not touched" vacuously true. Tolerance is Track A's established 0.5 s.

```
target  eligible  obs elapsed min..max   max |error|   dropped
5s      23        5.000 .. 5.000         0.000         1
10s     24        8.455 .. 10.001        1.545         0
30s     24        26.545 .. 33.456       3.456         0
60s     21        59.999 .. 60.000       0.001         3
5m      18        300.000 .. 303.454     3.454         6
10m     12        600.000 .. 603.454     3.454         12
15m     6         900.000 .. 903.454     3.454         18
```

### REQUIRED SUMMARY TABLE

```
h     eligible  long  short  either  both  either%  both%   long-only res  short-only res
5s    23        0     0      0       0     0.0%     0.0%    n=0            n=0
10s   24        0     0      0       0     0.0%     0.0%    n=0            n=0
30s   24        0     0      0       0     0.0%     0.0%    n=0            n=0
60s   21        0     0      0       0     0.0%     0.0%    n=0            n=0
5m    18        0     0      0       0     0.0%     0.0%    n=0            n=0
10m   12        0     0      0       0     0.0%     0.0%    n=0            n=0
15m   6         0     0      0       0     0.0%     0.0%    n=0            n=0
```

Touch % is a **TOUCH_UPPER_BOUND_F3**, never a fill probability.
`MAKER_FILL_PROBABILITY = NOT_IDENTIFIED`.
`PAIR_COMPLETION_PROBABILITY = NOT_IDENTIFIED`.

**Zero touches on either leg, at every horizon, over the full 17-minute block.**

### Why — descriptive, never promoted to a fill claim

The books did not move. Across all 24 pairs the touch price changed at all in
**2 of 24**, and both of those were the bid collapsing on the one degenerate
market. Five of six markets held an unchanged one-tick quote for the entire
block. A maker order is not being outrun here; nothing is trading.

### Sequential completion

```
FIRST_LEG_TOUCH_COUNT                        0 of 24
LONG_FIRST_COUNT                             0
SHORT_FIRST_COUNT                            0
COMPLEMENT_TOUCH_WITHIN_5M_AFTER_FIRST       NOT_OBSERVABLE_FROM_BLOCK_3
COMPLEMENT_TOUCH_WITHIN_10M_AFTER_FIRST      NOT_OBSERVABLE_FROM_BLOCK_3
COMPLEMENT_TOUCH_WITHIN_15M_AFTER_FIRST      NOT_OBSERVABLE_FROM_BLOCK_3
BOTH_LEGS_TOUCHED_BY_15M_FROM_T0             0 / 6
```

The conditional quantity is not merged with the unconditional one. With no
first touch there is no conditioning event, so the conditional is **not
observable from this block** — not zero, and not manufactured.

### Residual risk

No cycle produced a one-legged state, so there is no residual to distribute:
`n = 0` at every horizon. This is absence of the event, not a favourable
result.

### Completed-pair economics — conditional, not realized

Maker theta = −0.0125, a **rebate**. Long leg at p = b0, short leg at p = 1−a0;
the legs sit a spread apart so their rebates differ. Exact per-contract:

```
market                                  DISP_GROSS  reb_long    reb_short   total
asc-bun-…-fh-pos-2pt5                   0.0100      0.000245    0.000124    0.000369
asc-cfb-clmsn-cah-…-pos-17pt5           0.0050      0.000185    0.000124    0.000308
aec-ufc-gabste-seasha-2026-09-19        0.0100      0.001125    0.001024    0.002149
aec-atp-jjwol-lucamb-2026-09-14         0.0100      0.000920    0.000814    0.001734
asc-mlb-sd-col-…-pos-2pt5               0.0050      0.000185    0.000124    0.000308
aec-t20icr-eng-slr-2026-09-15           0.0100      0.002145    0.002074    0.004219
```

Rounding is half-even to the cent **per fill**, and it bites:

```
asc-bun-… (b0 0.9800 / a0 0.9900)      C=1 exact $0.00037 -> rounded $0.00
                                       C=10  $0.00369 -> $0.00
                                       C=100 $0.03687 -> $0.03
                                       C=1000 $0.36875 -> $0.36
aec-t20icr-… (b0 0.7800 / a0 0.7900)   C=1 exact $0.00422 -> rounded $0.00
                                       C=100 $0.42188 -> $0.42
                                       C=1000 $4.21875 -> $4.21
```

`ACTUAL_REBATE_NOT_IDENTIFIED` — no execution exists, fill fragmentation is
unknown, and a fragmented fill rounds to zero. Theoretical rebate is never
called realized revenue.

### PRE_ADVERSE_SELECTION_PAIR_BUDGET

`CONDITIONAL_ON_BOTH_MODELED_MAKER_EXECUTIONS`. Not profit, not expected
profit, not realized edge, not net expectancy. Per 100 contracts:

```
aec-t20icr-eng-slr-2026-09-15           $1.00 + $0.42 = $1.42
aec-ufc-gabste-seasha-2026-09-19        $1.00 + $0.21 = $1.21
aec-atp-jjwol-lucamb-2026-09-14         $1.00 + $0.17 = $1.17
asc-bun-…-fh-pos-2pt5                   $1.00 + $0.03 = $1.03
asc-cfb-clmsn-cah-…-pos-17pt5           $0.50 + $0.03 = $0.53
asc-mlb-sd-col-…-pos-2pt5               $0.50 + $0.03 = $0.53

LIQUIDITY_INCENTIVE_REWARD      = NOT_VERIFIED
EXCLUDED_FROM_PRIMARY_ECONOMICS = YES
```

### Capacity

```
market                            bid qty        ask qty       pair proxy
asc-cfb-clmsn-cah-…-pos-17pt5     0.1000         76.0100       0.1000
asc-mlb-sd-col-…-pos-2pt5         0.3400         463..2407     0.3400
asc-bun-…-fh-pos-2pt5             4.0100/1.0000  103.0000      1.00-4.01
aec-t20icr-eng-slr-2026-09-15     1036-1496      1015-1025      ~1016
aec-ufc-gabste-seasha-2026-09-19  2507-2550      5049-10598    ~2508
aec-atp-jjwol-lucamb-2026-09-14   14385-31398    148991        14386

DISPLAYED_CAPACITY_PROXY   the figures above
QUEUE_POSITION             NOT_IDENTIFIED
EXECUTABLE_CAPACITY        NOT_IDENTIFIED
```

Three of six markets show a bid side of **under five shares** — 0.10, 0.34 and
1.00–4.01. Displayed queue size is not executable capacity and establishes
nothing about where our order would sit.

### Segmentation

```
PRICE BAND      TAIL     20 pairs   either_touch<=15m 0   both 0
                MODERATE  4 pairs   either_touch<=15m 0   both 0
SPREAD REGIME   S_1T     24 pairs   0 / 0
TICK            0.005     8 pairs · 0.01  16 pairs        0 / 0
SPORT           6 groups of 4 pairs                        0 / 0 each
LEAGUE          atp, bun, cfb, mlb, t20icr, NOT_IDENTIFIED 0 / 0 each

NEAR_MID_EXECUTION_EVIDENCE_FROM_BLOCK_3 = NONE
```

The cohort contains zero NEAR_MID markets. Tail results are **not**
extrapolated to near-mid markets.

### Scope limitation, carried

`DISCOVERY_LIST_EXHAUSTED = NO`. BLOCK_3's selection came from a bounded prefix
of the current active universe. This is evidence about the selected cohort
only — not "the best markets on PMUS", not whole-board opportunity, not
current-universe expectancy.

---

## BLOCK_3 CLASSIFICATION

```
BLOCK_3_CLASSIFICATION = C — DISPLAYED EDGE ONLY
```

The displayed edge is real and measured: every one of the 24 pairs quoted a
one-tick spread, and the conditional budget per 100 contracts is $0.53–$1.42
including correctly-rounded maker rebates on both legs. Execution is entirely
unestablished: **0 touches on either leg at every horizon**, under F3 — the
most generous proxy available, where a touch is counted as a fill. There is no
first leg, so no completion evidence and no residual distribution.

Not D. Non-touch is absence of execution evidence, not adverse economics; no
observation in this block shows a passive pair losing money.

Not E. The data is clean and adequate to support the statement it makes — 120
reads, all 200s, spacing honoured, seal verified. What it cannot support is any
claim about fills.

```
DOES_BLOCK_3_JUSTIFY_CHANGING_48H_CLASSIFICATION = NO
```

Evidence: the 48-hour classification is already **C**, set from Track A
segment 2 (299 postable pairs, 1 long touch at 60 s, 0 both-touches). BLOCK_3
independently lands on C from a different cohort, a different design and longer
horizons. It adds no execution evidence in either direction, so there is
nothing to move the classification with. BLOCK_3 is also **not pooled** with
Track A — these are two separate C findings, not one larger sample.

The one thing BLOCK_3 does change is the *explanation*. Track A's non-touch was
attributed partly to very wide quotes (four of seven survivors at 50–100%
spread/mid). BLOCK_3 deliberately selected the tightest books available and
still saw zero touches, in markets whose quotes did not move at all for 17
minutes. Tightness was not the binding constraint; **trading activity was**.

---

## BL-SELECT-1 RETIRED · BL-SELECT-2 EFFECTIVE

```
BL-SELECT-1                  RETIRED_FOR_TRACK_B_L
REASON                       ACTIVITY_BLIND_SELECTION
EFFECTIVE UNTIL              BLOCK_3 (2026-09-14T17:13:39Z), inclusive
BLOCK_1 / BLOCK_2 / BLOCK_3  PRESERVED UNDER BL-SELECT-1, not reinterpreted

BL-SELECT-2                  EFFECTIVE FROM BLOCK_4 (2026-09-14)
```

### Why BL-SELECT-1 was retired

It scored candidates from the discovery payload alone, and `/v1/events` carries
**no activity field of any kind** — the only quantity in a market row is
`minimumTradeQty`. Ranking on tightest spread/mid therefore selects the markets
whose spread is narrow *because nobody is there*. BLOCK_3's cohort contained two
markets that had not traded in ~55 hours and three with negligible or no
lifetime volume, and returned **0 first-leg touches in 24 cycles**.

BLOCK_3 did **not** establish unfavourable maker economics. It established that
the selection rule was blind to the one variable that decides whether a passive
experiment can observe anything.

### What changed

**A stage 2 exists.** Discovery (stage 1) now produces a *prospective
shortlist*, and the runner then spends `/book` requests on that shortlist to
read the venue's own activity fields before anything is frozen.

**Stage 1 no longer ranks on spread/mid.** It stratifies by price band and ranks
by **imminence** — `|gameStartTime − now|` — which is knowable at selection time,
is not an outcome, and is the only prospective activity proxy discovery offers.
One market per event.

**Stage 2 reads fields that exist only in `/book`:**

```
sharesTraded · notionalTraded · openInterest · lastTradeSetTime
best bid / ask · displayed sizes · tick size · state
```

derived into `SECONDS_SINCE_LAST_TRADE`, `SHARES_TRADED`, `NOTIONAL_TRADED`,
`OPEN_INTEREST`, `SPREAD_TICKS`, `SPREAD_ABSOLUTE`, `SPREAD_OVER_MID`,
`BEST_BID_SIZE`, `BEST_ASK_SIZE`, each on a stamped selection-time receipt.
Receipts are sealed whether or not the block succeeds.

**Distributions are printed before thresholds are applied.** p10/p25/median/
p75/p90 for seconds-since-last-trade, notional, shares and open interest.

**The activity threshold is prospective twice over.** A market must have traded
inside `MAX_SECONDS_SINCE_LAST_TRADE = 3600 s` — a floor reasoned from the
20-minute block length and fixed before any data — *and* sit in the more active
half of what was actually probed today. Neither half looks at a touch outcome,
and the median is read off the distribution printed directly above it, so the
rule is auditable after the fact.

**Three components, never one score.** `ACTIVITY`, `LIQUIDITY` and
`DISPLAYED_PAIR_BUDGET` are computed by separate functions and reported
separately. A test asserts no `total_score` / `composite_score` / `rank_score`
identifier exists. Enormous volume with no maker cushion fails the economic
gate; a beautiful spread with no trading fails the activity gate.

**Diversity is targeted, never manufactured.** `NEAR_MID ≥ 2`, `MODERATE ≥ 2`,
`TAIL ≤ 2`, distinct events. The TAIL ceiling is *enforced*; the NEAR_MID and
MODERATE targets are *attempted*, and a board that cannot supply them yields a
smaller block with `BAND_TARGET_SHORTFALL_REASON =
BOARD_DID_NOT_SUPPLY_ELIGIBLE_MARKETS_IN_BAND`. A test proves a TAIL-only board
returns 2 markets, not 6 — it does not relax the ceiling to fill the block.

### Rate budget, computed before any venue contact

```
TRACK_A_RATE            0.2860 rps   its design maximum
B_L_DISCOVERY_RATE      0.4000 rps   16 pages at the 2.5 s floor
B_L_BOOK_PROBE_RATE     0.4000 rps   <=64 probes at the floor
B_L_CAPTURE_RATE        0.0952 rps   120 reads over 1,260 s

TOTAL_WORST_CASE_RATE   0.4000 rps   NOT A SUM
```

It is not a sum because the shared concurrency group is platform-level mutual
exclusion: at most one stream executes at any instant, and every B-L request
waits behind the same 2.5 s floor. The worst case is a single stream at the
floor, which is the established nominal. Adding the rates would describe a world
the group makes impossible.

```
REQUEST BUDGET     16 + 64 + 120 = 200 of 220
MIN_SPACING_S      2.5, unchanged and never weakened to fit the shortlist
pre-capture wall   200 s (3.3 min)
job wall           ~20.7 min, so a Track A segment now waits ~21 min, not ~18
```

Discovery was reduced from 26 pages to 16 to buy the probe budget — the trade is
a narrower discovery prefix for actual activity evidence, and
`DISCOVERY_LIST_EXHAUSTED = NO` continues to be reported and carried.

### Not changed

Market-type and structural eligibility, quote requirements, the economic
definitions, horizons, block size requirement, the 20-minute cap, the
no-cross-block-state rule, the shared concurrency group, the 2.5 s floor, and
Track A. Tests: **49 passing** under both the self-runner and pytest.

### BLOCK_4's question

Not profitability. **Does activity-aware selection produce markets in which
hypothetical resting orders interact with the market often enough for passive
execution to be measurable at all?** Primary outputs stay `FIRST_LEG_TOUCH`,
`BOTH_LEGS_TOUCH`, `COMPLEMENT_AFTER_FIRST`, `RESIDUAL_MARKOUT`, and touch
remains an upper-bound proxy, never a fill.

---

---

## BLOCK_4 ATTEMPT 1 — FAILED. PERMANENT RECORD.

```
B_L_BLOCK_4_ATTEMPT_1_STATUS = FAILED_SEALING_DEFECT
SCIENTIFIC_OBSERVATIONS      = 0
ECONOMICALLY_USABLE          = NO
CAPTURE_RAN                  = NO
BYTES_SEALED                 = NONE
BYTES_COMMITTED              = NONE
ARTIFACT                     = EMPTY (upload-artifact found no files)
```

Run `34880452606`, job `104098166408`, commit `06bbfa1`,
18:23:06Z → 18:24:43Z.

**The numbers below exist ONLY in the job log.** Nothing was sealed, so they are
transcribed here to preserve them; Actions logs expire and this would otherwise
be lost. They are log evidence, not sealed evidence, and are labelled as such
wherever they are used.

### The defect — mine, one line

```
line 941   blob = json.dumps(block, indent=1, sort_keys=True).encode()
           TypeError: Object of type Decimal is not JSON serializable
```

Every other `json.dumps` in the runner passes `default=str`; the cohort blob
does not. Under BL-SELECT-1 each selected market carried only strings, so it
never mattered. BL-SELECT-2 adds Decimal fields — `BEST_BID`,
`SPREAD_ABSOLUTE`, `REBATE_LONG/SHORT`, sizes — and the first attempt to freeze
the cohort raised.

**The second, worse half:** the crash is an *unhandled exception*, so it exited
before the sealing block that writes `discovery_pages`,
`discovery_body_samples`, `book_probe_receipts` and `block_summary`. The
`if: always()` commit step then had an empty directory to commit. This is the
same lesson as BLOCK_2 — evidence must survive the failure — in a new place: a
controlled non-zero exit seals, an exception does not.

### Why the requested audit cannot be performed

```
CHECKSUMS                       NOT_PERFORMABLE -- no sealed files
GLOBAL_MIN_INTER_REQUEST_GAP    NOT_IDENTIFIED
ANY_GAP_LT_2_5S                 NOT_IDENTIFIED
TRACK_A_OVERLAP_REQUEST_COUNT   0 by construction (shared group), NOT MEASURED
HTTP_429                        0 observed in the log
```

The per-request `local_request_wall_utc` stamps live in the probe receipts,
which were never written. Log-line timestamps are **not** a substitute: they are
printed after each response, so their spacing is request spacing plus latency
jitter, and several adjacent log lines are 2.2–2.5 s apart for that reason
alone. **The 0.4000 rps design claim is therefore NOT CONFIRMED by BLOCK_4** and
must not be treated as verified until a run seals its receipts.

### Timing, from the log

```
DISCOVERY_DURATION_S     37.6   (16 pages)
BOOK_PROBE_DURATION_S    37.4   (15 probes)
CAPTURE_DURATION_S       0      -- never ran
TOTAL_JOB_DURATION_S     97
```

The charter's cap, quoted exactly and not redefined after the fact:

> A block is capped at 20 minutes so that a Track A segment falling due
> mid-block waits minutes, not hours. Four 300 s cycles fit.

The cap governs the block — the life of the hypothetical orders — and no block
ran, so the cap was not exercised.

### Discovery limitation

```
DISCOVERY_LIST_EXHAUSTED   NO
EVENTS_SCANNED             1600
DISCOVERY_PAGE_CAP         16
CURRENT_UNIVERSE_CENSUS    NO
SELECTION_FRAME            BOUNDED_PREFIX
EVENTS_WITH_CLOSED_TRUE    0
MARKETS_TOTAL              14518   (14416 open, 102 resolved, 7739 quoted)
```

### What BL-SELECT-2 actually did — the signal

```
STRUCTURALLY_ELIGIBLE_MARKETS   339
SHORTLIST                       15      (NEAR_MID 3 · MODERATE 10 · TAIL 2)
BOOKS_PROBED                    15
```

The shortlist was capped by **the board, not the probe budget** — 15 distinct
events were available after one-market-per-event dedup, so only 15 of the 64
probe slots were spent.

Distributions, before any threshold:

```
                          p10          p25          median       p75          p90
SECONDS_SINCE_LAST_TRADE  128.7        287.0        704.0        1183.3       3358.8
NOTIONAL_TRADED           83,664       152,134      217,254      413,786      1,895,983
SHARES_TRADED             2,488.6      3,048.0      6,172.4      8,131.8      26,110.0
OPEN_INTEREST             1,526.3      3,982.8      6,783.8      14,798.9     29,478.0
```

**Against BLOCK_3, where two cohort markets had last traded 55 hours earlier and
three had 0.28 shares or no volume at all, the median BLOCK_4 probe had traded
11.7 minutes ago on $217k of notional.** That is the difference the rule change
was built to make, and it is visible in the distribution rather than argued for.

Gates, reported independently:

```
ACTIVITY_ELIGIBLE       4    rejects  NOTIONAL_BELOW_PROBED_MEDIAN 7
                                      SLOWER_THAN_PROBED_MEDIAN    6
                                      STALE_GT_3600s               1
ECONOMICALLY_ELIGIBLE   15   rejects  {}
FINAL_CANDIDATES        4
```

Every probed market passed the economic gate; activity alone did the cutting,
which is what the median-based rule is supposed to do.

### The block it selected

```
SELECTED_MARKETS   4     (>= REQUIRED_BLOCK_SIZE 4, below the target of 6)
NEAR_MID_COUNT     1     TARGET >= 2   NOT MET
MODERATE_COUNT     3     TARGET >= 2   MET
TAIL_COUNT         0     CEILING <= 2  MET

aec-ufc-josvan-alepan-2026-09-19  ev 87934  NEAR_MID mid 0.5450 1 tick
   since_last_trade 128.7 s · shares 38,184 · notional $2,080,297 · OI 36,229
   bid size 12,569.19 · ask size 4,427.67 · budget $1.62 / 100
aec-ufc-armtsa-mauruf-2026-09-19  ev 87935  MODERATE mid 0.7250 1 tick
   since_last_trade 287.0 s · shares 26,110 · notional $1,895,983 · OI 29,478
   bid size 15,114.53 · ask size 310.27 · budget $1.50 / 100
aec-ufc-renmoi-briort-2026-09-19  ev 87938  MODERATE mid 0.6550 1 tick
   since_last_trade 351.6 s · shares 6,401 · notional $413,786 · OI 6,784
   bid size 5,314.43 · ask size 1,545.48 · budget $1.56 / 100
aec-ufc-taitui-robdes-2026-09-19  ev 87943  MODERATE mid 0.1750 1 tick
   since_last_trade 704.0 s · shares 15,480 · notional $275,685 · OI 16,204
   bid size 7,056.49 · ask size 2,004.39 · budget $1.36 / 100
```

### Two things wrong with that cohort, flagged not fixed

1. **All four are fights on the same UFC card, dated 2026-09-19.** They are four
   distinct venue event ids, so the one-market-per-event rule was satisfied —
   but they are not four independent underlying contests in any economic sense.
   This is close to the "six contracts representing effectively the same
   underlying event" the charter warns against, and a result from this cohort
   would be one card's behaviour, not a cross-sport finding.
2. **`sport` and `league` are `NOT_IDENTIFIED` on all four** — UFC events carry
   a null `primaryTag`. The normalizer handled it correctly (carried, not
   crashed), but sport diversity in this block is nil.

Neither is repaired here. Both are recorded.

### gameStartTime semantics — NOT_IDENTIFIED for BLOCK_4

All four slugs are dated 2026-09-19 against a 2026-09-14 run, so the contests
are ~5 days out and were nonetheless trading minutes before selection. Per the
owner's instruction this is interpretation only and no market is excluded
retrospectively. The per-market `GAME_START_TIME` values live in the probe
receipts, which were not sealed, so
`DOES_GAME_START_TIME_REPRESENT_UNDERLYING_CONTEST_START = NOT_IDENTIFIED`
for every selected market until a run seals them.

### Touch analysis

`NOT_OBSERVABLE_FROM_BLOCK_4_ATTEMPT_1`. No capture ran, so there is no
`FIRST_LEG_TOUCH`, `EITHER_LEG_TOUCH`, `BOTH_LEGS_TOUCH` or
`COMPLEMENT_AFTER_FIRST` at any horizon. The comparison against BLOCK_3 that
this block exists to make has **not** been made.

---

### What this does not authorize

No live capital. No order placement. No `mirror_live` change. A tiny live
execution validation would be a separately approved step with an explicit
maximum exposure and stated kill conditions, and BLOCK_3 does not meet the bar
to propose one.

---

## BLOCK_4 ATTEMPT 2 — COMPLETE. AUDIT, PREFLIGHT, RESULT.

Run `06b1163`, commit `cfdee8f`, 19:48:59Z → 20:06:32Z.
`BLOCK_STATUS = OK`, `SCIENTIFIC_OBSERVATIONS = 80`, `ECONOMICALLY_USABLE = YES`.

### 1. Checksums against committed bytes

```
sha256sum -c checksums.sha256    8 / 8 OK
```

Eight files, including — for the first time — `book_probe_receipts.jsonl.gz`.
The sealing repair held.

### 2. Actual global request timing — AND A REAL VIOLATION

```
measurable requests             95   (15 probes + 80 capture)
GLOBAL_MIN_INTER_REQUEST_GAP    0.0546 s
ANY_GAP_LT_2_5S                 1
HTTP_429                        0
non-200 responses               0
TRACK_A_OVERLAP_REQUEST_COUNT   0 by construction, NOT MEASURED
```

**The 2.5 s floor was breached once, by 54.6 ms, at the probe→capture handoff**
— last probe 19:50:14.934Z, first capture read 19:50:14.988Z. `run_block()`
starts its pacer with `last = None`, so the first capture read fires
immediately rather than waiting 2.5 s after the final probe. Every other gap in
the block is ≥ 2.5 s.

```
B_L_AGGREGATE_RATE_SAFE = NOT_VERIFIED
```

The owner's condition was that the 0.4000 rps design claim is accepted **only
if actual timestamps confirm it**. They do not. One breach in 95 measurable
gaps is still a breach, and the claim stays unverified until a block runs clean.
It is not repaired here.

A second evidence gap: **discovery page records carry no per-request wall
stamp**, so 16 of the 111 requests are `NOT_MEASURABLE` from the seal. The
measured figures above cover probes and capture only.

### 3. Timing

```
DISCOVERY_DURATION_S     ~38    (16 pages; not stamped per request)
BOOK_PROBE_DURATION_S    35.0   (19:49:39.932 .. 19:50:14.934)
CAPTURE_DURATION_S       977.5  (19:50:14.988 .. 20:06:32.488)
TOTAL_JOB_DURATION_S     ~1053
```

The charter's cap, quoted exactly and not redefined after the result:

> A block is capped at 20 minutes so that a Track A segment falling due
> mid-block waits minutes, not hours. Four 300 s cycles fit.

Capture was 16.3 minutes, inside the cap. It is shorter than BLOCK_3's 17.4 min
because the block holds 4 lanes rather than 6.

### 4. Discovery limitation

```
DISCOVERY_LIST_EXHAUSTED   NO          EVENTS_SCANNED  1600
DISCOVERY_PAGE_CAP         16          EVENTS_WITH_CLOSED_TRUE  0
MARKET_ROWS  14518   OPEN 14408   RESOLVED 110   QUOTED 7696
CURRENT_UNIVERSE_CENSUS    NO
SELECTION_FRAME            BOUNDED_PREFIX
```

Carried into every conclusion below.

### 5. gameStartTime semantics — now sealed and readable

```
market                                 GAME_START_TIME        TYPE
aec-ufc-josvan-alepan-2026-09-19        2026-09-20T03:15:00Z   MONEYLINE
aec-ufc-taitui-robdes-2026-09-19        2026-09-19T23:30:00Z   MONEYLINE
aec-ufc-renmoi-briort-2026-09-19        2026-09-20T01:30:00Z   MONEYLINE
aec-ufc-gabste-seasha-2026-09-19        2026-09-20T00:30:00Z   MONEYLINE

DOES_GAME_START_TIME_REPRESENT_UNDERLYING_CONTEST_START = YES (all four)
```

Distinct, plausible, sequential fight times on one card — 23:30, 00:30, 01:30,
03:15 — consistent with a UFC running order. Interpretation only; nothing was
excluded retrospectively.

### BL-SELECT-2 PREFLIGHT

```
STRUCTURALLY_ELIGIBLE_MARKETS   339
SHORTLIST                       15      (board-limited, not budget-limited)
BOOKS_PROBED                    15
```

Raw stage-2 distributions, before any threshold (n=14; one probe returned no
trade fields at all):

```
                          p10          p25          median       p75          p90
SECONDS_SINCE_LAST_TRADE  267.5        568.2        948.0        2227.0       3316.5
SHARES_TRADED             2779.7       3048.0       6223.2       15560.2      27458.4
NOTIONAL_TRADED           94,241       157,263      218,484      640,704      1,993,175
OPEN_INTEREST             1813.7       4046.8       6932.4       16284.2      30716.9
```

Gates, independently:

```
ACTIVITY_GATE     eligible 4   NOTIONAL_BELOW_PROBED_MEDIAN 6 · SLOWER_THAN_
                              PROBED_MEDIAN 6 · STALE_GT_3600s 1 ·
                              NO_LAST_TRADE_TIME 1 · NO_TRADED_NOTIONAL 1 ·
                              NO_OPEN_INTEREST 1
LIQUIDITY + DISPLAYED_PAIR_BUDGET GATE    eligible 15   rejects {}
FINAL_CANDIDATES  4
```

```
SELECTED_MARKETS 4 · DISTINCT_EVENTS 4
NEAR_MID 1 (target >=2 NOT MET) · MODERATE 2 (MET) · TAIL 1 (ceiling MET)
BAND_TARGET_SHORTFALL_REASON = BOARD_DID_NOT_SUPPLY_ELIGIBLE_MARKETS_IN_BAND
SPORTS ['NOT_IDENTIFIED'] · LEAGUES ['NOT_IDENTIFIED'] · SPREAD_REGIMES ['S_1T']
SELECTION_RULE_VERSION BL-SELECT-2 · SUPERSEDES BL-SELECT-1 (RETIRED)
SELECTION_RULE_UNCHANGED_WITHIN_BLOCK YES · BLOCK_FROZEN YES
RATE_GATE PASS (as designed) · SHARED_CONCURRENCY_GROUP YES
```

Per market, at selection:

```
josvan-alepan  NEAR_MID 0.5400/0.5500 1t  last trade 568.2 s  39,025 sh
               $2,125,993  OI 36,371  bid 22,297.32  ask 4,003.89  budget $1.62
taitui-robdes  MODERATE 0.1700/0.1800 1t  last trade  96.8 s  15,560 sh
               $277,128    OI 16,284  bid  6,682.90  ask 2,187.78  budget $1.36
renmoi-briort  MODERATE 0.6500/0.6600 1t  last trade 563.0 s   6,630 sh
               $428,887    OI  6,932  bid  7,388.24  ask 2,256.70  budget $1.56
gabste-seasha  TAIL     0.9000/0.9100 1t  last trade 652.8 s  16,452 sh
               $1,484,225  OI 19,497  bid  2,318.64  ask 10,917.10 budget $1.21
```

All four are fights on the same UFC card, and `sport`/`league` are
`NOT_IDENTIFIED` (null primaryTag). Both were flagged on attempt 1 and are
unchanged.

### TOUCH ANALYSIS — with denominators

```
h     eligible  long  short  either  both     BLOCK_3 comparison (eligible)
5s    16        0     0      0       0        0 / 23
10s   16        0     0      0       0        0 / 24
30s   16        0     0      0       0        0 / 24
60s   16        0     0      0       0        0 / 21
5m    12        0     0      0       0        0 / 18
10m   8         0     0      0       0        0 / 12
15m   4         0     0      0       0        0 / 6
```

`FIRST_LEG_TOUCH 0 / 16` · `COMPLEMENT_AFTER_FIRST NOT_OBSERVABLE` ·
residual `n = 0`. Horizon clock error ≤ 0.002 s on every in-cycle horizon.

**And the quote did not move once: 0 of 16 pairs saw either touch price change
in 16.3 minutes.**

### WHY — AND WHY THE TOUCH PROXY IS THE WRONG INSTRUMENT HERE

This is not BLOCK_3 repeating. BLOCK_3's markets were dead. These traded. The
difference is that a **deep one-tick book does not have to move for a resting
order to fill** — the queue drains instead — and a price-crossing test cannot
see that at all.

So the sealed books were asked the question directly:

```
market            bid queue    traded in block   markets printing any trade
josvan-alepan     22,297.32    180.33 shares     in 16.3 min: 1 of 4
taitui-robdes      6,682.90    0
renmoi-briort      7,388.24    0
gabste-seasha      2,318.64    0
```

**Three of the four "most active" markets on the eligible board printed ZERO
trades in 16.3 minutes**, having last traded 96–653 s before selection. The one
that traded moved 180.33 shares — both sides combined — against a 22,297-share
bid queue.

Treating that cumulative both-sides volume as if it all drained our side (a
deliberately generous upper bound):

```
QUEUE_DRAIN_TIME_UPPER_BOUND   22,297 / 11.1 per min = 2,014 min = 1.4 days
```

A passive order joining that bid would have been behind ~22,287 shares and
would have received **none** of the 180 that traded.

```
MAKER_FILL_PROBABILITY        NOT_IDENTIFIED
PAIR_COMPLETION_PROBABILITY   NOT_IDENTIFIED
```

Still not identified — but for the first time the *reason* is quantified: it is
queue position in a deep one-tick book, not market inactivity.

### BLOCK_4 CLASSIFICATION

```
BLOCK_4_CLASSIFICATION = C — DISPLAYED EDGE ONLY
BL_SELECT_2_WORKED     = YES, as a selection rule
PASSIVE_MAKER_PATH     = SUSPECT AS THE NEAR-TERM PRODUCTION ROUTE
```

BL-SELECT-2 did its job: it found genuinely traded markets, and the activity
distribution moved by orders of magnitude. The strategy is what failed to show
interaction. Under the owner's own decision rule — *"if active-market selection
still produces essentially zero interaction, the passive maker/maker path itself
becomes suspect as the fastest route to production"* — that branch fires.

The economics remain what they were and are not restated as new: a $1.21–$1.62
displayed budget per 100 contracts, `CONDITIONAL_ON_BOTH_MODELED_MAKER
_EXECUTIONS`, against a queue that would take on the order of a day to clear at
observed volume.

Carried with every line above: `DISCOVERY_LIST_EXHAUSTED = NO`, selection from a
bounded prefix, one UFC card, `NEAR_MID` target unmet, and a rate floor breach
that leaves `B_L_AGGREGATE_RATE_SAFE = NOT_VERIFIED`.

No live capital. No order placement. `mirror_live = false`.
