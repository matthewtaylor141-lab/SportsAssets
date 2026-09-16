# BETA48 FORWARD — corrections of record

Every entry here is something this programme asserted and later found wrong.
They stay attached permanently. A correction that is only made in passing is
not a correction, because the wrong number outlives the conversation it was
said in.

---

## C-1. The three-tick discount figure (arithmetic)

**Asserted:** at `discountFactor = 0.30`, a quote three ticks back scores
**4.05%** of the same size at the touch.

**Correct:** **2.7%**. The ladder is a pure power of the factor:

| ticks from best | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `0.30^n` | 1.000 | 0.300 | 0.090 | **0.027** |

4.05% is not a term in this formula at all (it would be `0.30^2 x 0.45`).

**What survives:** the `~37x` size statement. `1 / 0.027 = 37.04`, so a quote
three ticks back still needs about thirty-seven times the size to match one at
the touch — far more inventory risk for the same score. The conclusion was
right; the percentage it was quoted with was not.

**Where the error was and was not.** The code was always correct:
`liquidity_score(500, 3, 0.30)` returned `13.500`, which is exactly 2.7% of
500. The wrong figure appeared only in narrative reporting. Pinned now in
`test_fees_v2.py::test_the_discount_ladder_exactly` and
`test_depth_panel.py::test_the_discount_ladder_is_exact`, both of which assert
`0.30^3 == 0.027` and explicitly assert it is *not* `0.0405`.

---

## C-2. "Whole population" language for a capped walk

**Asserted:** the 20,000-market board walk was "the whole population", "the
whole board", exchange-wide complete.

**Correct:** the walk stopped at the 200-page cap with
`DISCOVERY_LIST_EXHAUSTED = NO`. The honest statement is:

```
OBSERVED_PREFIX_MARKETS          >= 20,000 OPEN MARKETS
TRUE_ACTIVE_BOARD_SIZE           NOT_IDENTIFIED
BREADTH_IS_EXCHANGE_WIDE_COMPLETE NO
```

Breadth coverage is still **unbiased within the observed prefix** — there is no
selection step inside it — and that is worth having. It is not the same claim
as exchange-wide completeness, and calling it one would be the archaeology's
own error in a friendlier font.

`TRUE_ACTIVE_BOARD_SIZE` becomes a number only when discovery reaches a real
terminal boundary. Pinned in
`test_fwd_collect.py::test_a_capped_walk_is_a_prefix_and_says_so_in_those_words`.

---

## C-3. The zero-selection defect was misdiagnosed once

**Asserted:** the first live run selected zero markets because pagination was
not advancing, so duplicate pages made each event look like it had 200
outcomes.

**Correct:** 20,000 rows carried 20,000 **distinct** slugs. Pagination advanced
perfectly. The real defect was that no row carries `eventSlug` at all, so
grouping by it produced an empty dict. The dedupe guard added at the time was
harmless but was not the fix; it is retained as a guard against a failure mode
this venue does not exhibit, and its test says so.

---

## C-4. The incentives endpoint was read at the wrong level, and as a prefix

**Asserted:** the first incentives capture recorded a full row of nulls for
every programme field.

**Correct:** every economic field lives inside `timePeriods`, not on the
market, and markets carry 1, 4 or 5 periods with different pools and factors.
Separately, the endpoint is token-paginated and the first capture followed
`nextPageToken` zero times — the same prefix mistake as C-2, in a new place.
Both fixed; both pinned.

---

## C-5. A rotating page token inflated the incentive sample 200x

**Segment 35043611049 is VOID for incentive counts.** It reported:

```
incentive_pages_walked   200   (the cap)
INCENTIVES_LIST_EXHAUSTED NO
incentivized_markets     100
programs_parsed          53,400
```

100 markets cannot yield 53,400 distinct programme-periods. The endpoint
returned a **different `nextPageToken` on every request while serving the same
100 markets**, so the walk ran to the page cap and the same ~267 periods were
recorded 200 times over.

**Why the existing guard missed it.** C-4's fix stopped only when the token
*repeated*. A rotating cursor never repeats, so the guard never fired. The
board walk already terminates on **content** — a page contributing no new slug
ends the walk — and that lesson was not carried across to the token walk. It is
the third time in this programme that a prefix/duplication guard existed in one
place and was missing in another.

**Fixed both ends:**
- the walk ends when a page contributes no new
  `(marketSlug, programId, programType, period, start)`, recording
  `INCENTIVES_PAGINATION_ADVANCED`;
- records are deduped on the way out too, so a repeated page can never become a
  repeated record — a distribution over duplicates reads as a sample two
  hundred times larger than anything observed.

Pinned in `test_fwd_collect.py::test_a_rotating_page_token_over_identical_content_ends_the_walk`.

**What survives from that segment.** The *shape* of the distribution, which was
already established on the single clean page of segment 35042094434 and is
unchanged: discount factors are only 0.3 and 0.35; pools are 500 / 1000 / 1250
/ 10000; target sizes 500 / 1000 / 20000. The **counts** do not survive and are
not reported.

---

## C-6. "Tonight" — an off-by-one-day paraphrase of the fee cutover

**Said:** that the fee cutover was "tonight, 03:59 UTC", in reports written
from captures stamped 2026-09-16 in UTC.

**Correct:** the cutover is **23:59 ET on Wednesday 2026-09-16**, which is
**03:59 UTC on Thursday 2026-09-17**. At the time those reports were written
it was the evening of **Tuesday 2026-09-15** in ET, and the cutover was
**~26 hours away** — not that night, and not 03:59 UTC on the 16th.

**Where the error was and was not.** The code was right throughout:
`REGIME_CUTOVER_UTC = 2026-09-17 03:59Z` and the collector's
`FEE_REGIME_CUTOVER_UTC` both carried the correct instant, and every captured
segment was correctly labelled `JUL2026`. The error was in narration only.

**Why it matters more than a typo.** The ET date and the UTC date of this
cutover **differ**. Reading a UTC capture stamp (`2026-09-16T00:29Z`) and
assuming the cutover shares that date puts the new regime a full day early —
after which a correctly captured `feeCoefficient = 0.06` reads as though it
*contradicts* the documentation, when it is in fact the correct current value.
That is a defect that manufactures a false anomaly.

**Pinned** in `test_fees_v2.py::TheCutoverMoment`: the ET weekday and the UTC
weekday are asserted to be Wednesday and Thursday respectively, the two dates
asserted to differ, `03:59 UTC on the 16th` asserted to still be `JUL2026`,
every hour of the 16th UTC asserted `JUL2026`, and each of the four capture
timestamps we already hold asserted pre-cutover.

Preserved until a post-cutover observation:

```
CURRENT_CAPTURED_TAKER_THETA   0.06
UPCOMING_STANDARD_TAKER_THETA  0.0695
UPCOMING_EFFECTIVE_TIME        2026-09-16 23:59 ET (Wednesday)
UPCOMING_EFFECTIVE_TIME_UTC    2026-09-17 03:59 UTC (Thursday)
VERIFIED_FROM_CAPTURE          False
```

---

## C-7. The combo curve is UPCOMING, and its benefit was nearly prejudged

Two faults in one paragraph.

**(a) Schedule.** I presented the combo curve
`Fee = C × p × [0.0695(1-p) + 0.04(1-p)^4]` as though it described a current
cost. It does not: it sits under the page's "Upcoming fee changes" callout and
takes effect at the same cutover as the standard coefficient. Renamed
`UPCOMING_COMBO_TAKER_FEE_FORMULA`; `CURRENT_COMBO_TAKER_FEE_FORMULA` is
`NOT_IDENTIFIED` and is **not** back-formed by substituting 0.06.

**(b) Conclusion.** I wrote that a combo is "a more expensive way to acquire
the same legging risk". That prejudges the benefit as zero while
`COMBO_EXECUTION_ATOMICITY` and `COMBO_PARTIAL_FILL_BEHAVIOR` are both
unmeasured — the mirror image of the error this programme spends most of its
effort avoiding in the other direction. A venue charging a premium for a paired
instrument may be charging for real risk transfer.

Replaced with:

```
UPCOMING_COMBO_TAKER_COST_PREMIUM       VERIFIED_FROM_PRIMARY_DOCS
COMBO_ORPHAN_RISK_REDUCTION             NOT_IDENTIFIED
COMBO_EXECUTION_ATOMICITY               NOT_IDENTIFIED
COMBO_PARTIAL_FILL_BEHAVIOR             NOT_IDENTIFIED
COMBO_NET_VALUE_VS_SINGLE_LEG_EXECUTION NOT_IDENTIFIED
```

**(c) Precision.** The far-tail premium is **+0.06%** at p = 0.90, not the
+0.1% I reported. Rounding up nearly doubles it, and it is the one price where
the premium is negligible. Pinned to 0.0576% in
`test_fees_v2.py::TheUpcomingComboPremium`.

---

## C-8 — the panel read one book once per PROGRAMME, and counted each copy

**Class: duplication. This is the fourth time in this programme that a
duplication guard existed in one place and was missing in another** (the board
walk, the incentives token walk, the incentives emit side, and now the panel).
The pattern is the finding; the instance is just where it surfaced this time.

**What the venue actually does.** It runs several concurrent liquidity
programmes on ONE market. Captured from segment 35045117108's own
`/v1/incentives` bytes, `aec-ufc-alomen-iwobar-2026-09-19` carries five active
periods at the same instant:

```
ufc_main_moneyline_early_20260712   early    pool  1250  target 20000  df 0.30
ufc_main_moneyline_dayof_20260915   day_of   pool  1250  target 20000  df 0.30
ufc_main_moneyline_live_20260915    live     pool 10000  target 20000  df 0.30
ufc_main_props_dayof_20260915       day_of   pool   500  target  1000  df 0.35
ufc_main_props_live_20260915        live     pool   500  target  1000  df 0.35
```

Stratifying over programmes is right — they are genuinely different economics.
**Reading the book once per programme is not.** The collector fetched the same
market's book up to five times a round, ~21 s apart, and wrote each as its own
`PANEL` row.

**Two separate harms.**

1. **Venue cost.** 324 book requests bought 72 distinct book snapshots. 4.5x
   the traffic for the same information, against a 2 rps ceiling that is the
   binding constraint on how much of the board a segment can reach.

2. **Silent non-uniform weighting — the one that changed a number.** The report
   counted 324 observations. Each market therefore entered the statistics
   weighted by how many programmes it happened to carry, 4 or 5, which is a
   fact about venue programme design and nothing to do with book depth.

**What the pooled figure was hiding.** Eligibility is a FUNCTION OF THE TARGET
SIZE, and these markets run two targets at once. Pooling them produced a number
describing no programme anyone can actually quote into:

```
                              pooled (wrong)   target 1,000   target 20,000
ASK one tick back                    79.6%          66.7%          91.7%
BID one tick back                    71.2%          65.7%          79.3%
```

The pooled value sits between two real answers that differ by 25 points.

**Fixed at both ends.** `panel_cohort` now returns
`(reads, plan, slots)`: strata and their slots are unchanged, and the READ list
is collapsed to distinct markets carrying all their programmes. `panel` writes
ONE row per (market, round) with a `PROGRAMS` list, `PROGRAMS_N` and
`DISTINCT_TARGET_SIZES`. `panel_report` reads BOTH row shapes — so the
correction applies to the segments already on disk, not only to segments not
yet captured — dedupes on `(slug, round, TARGET_SIZE)`, and reports each target
size in its own block. There is no code path left that prints a blended figure.

**Segment 35045117108 is NOT void.** Unlike C-5, no information was lost: the
duplicate rows are real reads of a real book, and rescoring them under the
correct unit recovers the whole panel. Only the previously reported percentages
are superseded, by the per-target table above.

**Two further limits of that panel, stated rather than left to be assumed.**

- `SPORT_CONCENTRATION = [('ufc', 12)]`. Nine strata and 54 slots resolved to
  **12 distinct markets, every one a UFC fight**, because slug order within a
  stratum is deterministic and `aec-ufc-` sorts early enough to fill every
  stratum. That is not selection bias — no outcome touched the choice — but the
  panel measures one event family's books, so `PANEL_GENERALIZES_TO_BOARD = NO`.
- The panel spans **6 rounds x 30 s = 2.5 minutes**. Across 60 consecutive
  snapshot pairs the touch price moved **zero times**. That is a statement
  about a 2.5-minute window and is emphatically **not** a touch rate.

## C-8b — I understated what a REST snapshot carries

A correction to my own capability assessment, in the direction of being too
pessimistic, which is just as wrong as the other direction.

The book payload carries a `stats` block: `sharesTraded`, `notionalTraded`,
`lastTradePx`, `lastTradeQty`, `lastTradeSetTime` (nanosecond), `openInterest`
and their set-times. Differencing two snapshots therefore establishes THAT
trading occurred in the interval and HOW MUCH. I had assigned LEVEL_0 no trade
information at all.

It moves exactly one cell: `DEPTH_DEPLETION` from `NO` to `PROXY_ONLY` at
LEVEL_0. It is a polled volume counter, not a tape, and it does not give:

```
TRADE_AGGRESSOR_AVAILABLE           NO   (last trade price beside a one-tick
                                          spread does not identify the lifter)
PER_TRADE_SEQUENCE                  NO   (several trades collapse into one
                                          sharesTraded delta)
PASSIVE_FILL_ATTRIBUTION_AVAILABLE  NO   (it never says WHOSE order filled)
TRADE_COUNTER_IS_A_TAPE             NO
```

Reported in `panel_report.tape_observables`, with each of those four NOs pinned
as a test so the counter cannot be quietly promoted into a tape.

---

## C-9 — three precision corrections, applied to the NEXT step, not the running one

Independently confirmed against current official Polymarket US documentation.
The segment running when these landed was NOT modified: the selection change
below is opt-in and defaults to the original rule, so a dispatch that does not
ask for it — including one already in flight — behaves exactly as before.

### C-9a. LEVEL_4 MBO proves a COUNTERFACTUAL fill, not BETTOR's fill

FIX market data is genuinely Market-by-Order — every order individually
represented, unique OrderID, a per-order timestamp documented as setting time
priority within a price level, incremental New/Change/Delete, Trade entries
with AggressorSide. That is dramatically stronger than aggregated L2, and it is
still not evidence about an order that was never submitted.

```
COUNTERFACTUAL_PASSIVE_FILL   what a simulator concludes about a quote that
                              was never submitted
ACTUAL_BETTOR_PASSIVE_FILL    what the venue did with an order BETTOR placed
```

`LEVEL_4_MBO_WITHOUT_BETTOR_ORDER` now carries all eleven terms explicitly,
with `ACTUAL_BETTOR_PASSIVE_FILL`, `ACTUAL_BETTOR_ORDER_ACCEPTANCE_LATENCY` and
`ACTUAL_BETTOR_FILL_PROBABILITY` all `NO`, and
`TRUE_QUEUE_POSITION_FOR_HYPOTHETICAL_ORDER` carrying its condition rather than
an unconditional YES. `FIX_MBO_GIVES = HIGH_FIDELITY_COUNTERFACTUAL_FILL
_SIMULATION`; `FIX_MBO_DOES_NOT_GIVE = DIRECTLY_OBSERVED_BETTOR_FILL
_PROBABILITY`. Those three NOs close only when BETTOR submits real passive
orders:

```
MICRO_LIVE_REQUIRED_FOR_FINAL_EXECUTION_VALIDATION = YES
MICRO_LIVE_AUTHORIZED                              = NO
```

Recording a requirement is not authorisation to meet it. Writing it down is
what stops a data-quality upgrade being mistaken for having cleared the gate.

### C-9b. The running panel is UFC-local evidence — and the two failures are different

`OUTCOME_LEAKAGE` and `COVERAGE_BIAS` are not the same defect and no longer
share a field:

```
PANEL_GENERALIZES_TO_BOARD = NO
PANEL_SPORT_SCOPE          = UFC
PANEL_SELECTION_METHOD     = LEXICOGRAPHIC_WITHIN_FROZEN_STRATA
OUTCOME_LEAKAGE            = NO
COVERAGE_BIAS              = YES
DO_NOT_USE_AS              = EXCHANGE_WIDE_OR_SPORTS_WIDE_ESTIMATE
```

Its target-size eligibility, depth, touch, markout, adverse-selection and
maker-budget figures are valid UFC/local microstructure and are not
exchange-wide or sports-wide estimates. `panel_report.py` prints all six lines
beside every figure, so a number cannot travel away from the label bounding it.

### C-9c. The next panel ranks on a frozen salted hash, not the alphabet

Lexicographic order is deterministic and outcome-blind. It is **not
identity-blind**: `aec-ufc-` sorts early enough to fill every stratum.

The strata stay exactly as they were. Within a stratum the order becomes
`SHA256(PANEL_SALT + "|" + market_slug)`, lowest first.

```
PANEL_SALT = "BETA48-FORWARD-PANEL-2026-09-16"
```

The salt is a literal in `fwd_collect.py`, fixed before any outcome from a
hash-selected panel exists. Changing it re-randomises the sample, so changing
it after seeing a result would be resampling until the answer is liked. It
does not change again.

`panel_composition()` reports `SPORT_DISTRIBUTION`, `LEAGUE_DISTRIBUTION`,
`PROGRAM_TYPE_DISTRIBUTION`, `TARGET_SIZE_DISTRIBUTION`,
`TICK_SIZE_DISTRIBUTION`, `UNIQUE_MARKETS`, `UNIQUE_EVENTS`,
`MAX_SINGLE_SPORT_SHARE` and `MAX_SINGLE_LEAGUE_SHARE` into `panel_plan.json`.

**If a hash-selected panel is still concentrated, that is reported, not
resampled.** `CONCENTRATION_IS_REPORTED_NOT_RESAMPLED = YES`, pinned by a test
that draws from a single-sport universe and asserts the artifact says 100%.
Resampling until a sample looks diverse is selection by another name, and a
balanced panel would be a DIFFERENT experiment requiring its own preregistration.

### C-9d. gRPC `unaggregated` stays unresolved

`unaggregated=True` is documented as "receive raw order book" while the
`BookEntry` schema carries only `px` and `qty` with `qty` described as the
aggregate at that level. Unreconciled, and not promoted:

```
GRPC_UNAGGREGATED_EXISTS                   YES
GRPC_UNAGGREGATED_EXACT_SEMANTICS          NOT_IDENTIFIED
GRPC_UNAGGREGATED_HAS_ORDER_ID             NO DOCUMENTED FIELD
GRPC_UNAGGREGATED_HAS_ORDER_TIMESTAMP      NO DOCUMENTED FIELD
GRPC_UNAGGREGATED_GIVES_TRUE_TIME_PRIORITY NOT_ESTABLISHED
```

The resolving experiment is specified BEFORE the credential exists, so it
cannot be designed in the excitement of finally holding one.

### C-9e. A documented capability is not a grant

```
VENUE_ENFORCED_READ_ONLY_CAPABILITY = DOCUMENTED
CAPABILITY_DOCUMENTED               = YES
BETTOR_GRANTED_SCOPE                = NOT_IDENTIFIED
BETTOR_CREDENTIAL_INSTALLED         = NO
BETTOR_CONNECTION_AUTHORIZED        = NO
```

Four separate facts. The first is about the venue; the rest are about us.

---

## C-10 — the public execution tape, verified from our own bytes

**Confirmed, and it is not what the relay described.** The claim was checked
against `docs.polymarket.us/faqs/execution-tape.md`, response sha256
`5ff446a6abd4fbca...`, in the 340-page capture on branch
`beta48-capability/docs-35047389408`.

The four columns and the three absences are exactly as relayed:

```
Transaction Time | Symbol | Last Price | Last Quantity
and explicitly NO side, NO aggressor flag, NO buyer or seller
```

**Three things the captured page adds, each of which changes the design.**

1. **It is a file, not an endpoint.** A daily CSV, `YYYYMMDD-time-and-sales
   .csv`, offered from `www.polymarketexchange.com` — a THIRD host we had never
   contacted, alongside the gateway and the docs host. So this is a
   retrospective join, not a live feed, and it needs its own capability
   boundary rather than a corner of an existing one.
2. **The business date is not a UTC day.** The venue's reporting occurs "as of
   5:00 PM Eastern Time each business day" (captured: `learn/trading/
   access-and-limits/trading-hours.md`). A file named `20260113` therefore does
   NOT cover 2026-01-13 00:00–24:00 UTC. **This is C-6 in a new place** — an
   off-by-a-day that misaligns the entire tape by up to seven hours, silently,
   in a direction that looks like ordinary noise. `tape_capture.py` performs no
   date arithmetic at all, and a test proves it by AST rather than by scanning
   for a string the file legitimately contains.
3. **No download URL is documented, only a landing page.** The docs give a
   filename CONVENTION. Turning a convention into a URL means guessing a path
   on a host we have never fetched, where a guessed 404 is indistinguishable
   from "the venue does not publish this" and a guessed 200 on the wrong path
   is worse. The capturer fetches the landing page and follows ITS links; if
   the page links nothing it reports `NO_LINKS_FOUND`. A test walks the AST for
   any f-string, `%` or `.format()` that builds a `.csv` path and fails if one
   exists.

**A second free artifact found in the same capture:** the Daily Market Report,
21 columns including open interest, settlement price and the day's low/high bid
and offer.

**The three questions that decide whether any of this is usable** stay open
until a real file is read, and the third is the single point of failure — if
the tape's `Symbol` does not join to the board's market slug, the pipeline
yields nothing:

```
TAPE_TIMESTAMP_PRECISION          NOT_IDENTIFIED   (so MARKOUT_1S may not be
                                                    measurable at all)
TAPE_PUBLICATION_LATENCY          NOT_IDENTIFIED
TAPE_SYMBOL_JOINS_TO_MARKET_SLUG  NOT_IDENTIFIED   <- single point of failure
```

**Scheduling note for the free cutover test.** Maintenance runs Thursday
02:00–04:00 ET. The coefficient flip is Wednesday 23:59 ET, so the verifying
segment must land in the ~2-hour window before 02:00 ET, or after 04:00 ET.

## C-11 — my own F2 credited a fill where nothing traded

Caught by the monotonicity sweep in `test_fill_model_v2.py`, not by reading the
code. The first version of F2 read:

```python
f2 = f1 or (removed is not None and (at + removed) > (ahead + added))
```

With no prints at all and a queue ahead cancelled away, that returned
`COUNTERFACTUAL_FILL_SUPPORTED_F2 = YES` while `F3_TOUCH_ONLY`, its own upper
bound, was `NO`.

**The substantive error, not the arithmetic one:** a queue that empties by
cancellation moves a maker to the FRONT of the queue. That is not being filled.
A maker at the front of an untouched queue holds exactly zero contracts. F2 now
additionally requires `at > 0` — somebody actually traded at our price — and
three tests pin it, including one asserting F2 can never exceed F3.

The monotonicity invariant `F0 <= F1 <= F2 <= F3` is asserted at runtime inside
`evaluate()` as well as in the sweep, so a future edit that breaks it raises
rather than quietly reporting support above its own ceiling.

## C-12 — the Target Size documentation conflicts with itself

Two official pages describe the same parameter incompatibly:

```
TARGET_SIZE_GENERIC_PAGE             MAXIMUM_DESCRIPTION
TARGET_SIZE_DETAILED_LIQUIDITY_PAGE  MINIMUM_AGGREGATE_THRESHOLD
DOC_CONFLICT_TARGET_SIZE             YES
TARGET_SIZE_IMPLEMENTED_FROM         DETAILED_LIQUIDITY_PROGRAM_PAGE
DOC_CONFLICT_TARGET_SIZE_RESOLVED_BY NOT_IDENTIFIED
```

We implement the detailed page because it is the one that states the
procedure — walk outward from best, accumulate RAW size, stop at Target Size,
everything inside the range scores and everything beyond it scores zero. We do
NOT rewrite either page to match the other, and we do not retire the conflict
on our own authority.

**Why it is not bookkeeping.** The two readings disagree about WHICH ORDERS
SCORE AT ALL, so every depth-panel eligibility figure is conditional on the
detailed page being the operative one. That conditionality is now a label
rather than an assumption.

## C-13 — mass quote protection does not cap inventory

Executions accumulate over a rolling interval and the remaining resting orders
cancel once the threshold is breached — but **the execution that breaches it
still completes**, so a single aggressive sweep can fill for MORE than the
threshold before any cancellation happens.

```
MQP_IS_HARD_MAX_FILL_LIMIT              False
MQP_TRIGGERING_EXECUTION_COMPLETES      True
MQP_IS_SUFFICIENT_AS_SOLE_INVENTORY_CAP False
```

An inventory cap that does not bound the worst single fill is not an inventory
cap. BETTOR's own seven controls stay required as an independent layer:
position, event, correlation and loss limits, stale-data kill, quote-age limit,
inventory kill.

---

## C-14 — the tape carries block trades, and a block depletes no queue

**A correctness defect in what I built yesterday**, not a refinement of it.

A block trade executes APART FROM the public order book and does not touch
orders resting in it — and is reported into Time & Sales anyway. So:

```
TIME_SALES_VOLUME  !=  ORDER_BOOK_DEPLETION
```

A maker sitting in the queue is exactly as far from the front after a block
prints as before it. The first version of `fill_model_v2.py` counted every
tape print toward queue depletion, so a single large block would have
manufactured a counterfactual fill for a quote nothing ever touched.

**Worst exactly where it matters most.** The large, illiquid, block-traded
markets are the ones where a maker's queue position is most valuable, and they
are the ones whose tape carries the biggest blocks.

**What I verified myself, and what I did not.** Blocks exist: FIX
`ExecInst = j`, "SINGLE EXECUTION REQUESTED FOR BLOCK TRADE", and the Daily
Market Report carries a separate `Block Volume` column beside `Trade Volume`
— both in the 340-page capture. That establishes the contamination risk. That
blocks appear in Time & Sales, and any row-level flag for them, I could NOT
verify: no Block Trade Data page appears in `llms.txt` or in any captured page.

```
BLOCK_TRADES_EXIST                     VERIFIED_FROM_CAPTURE
BLOCKS_APPEAR_IN_TIME_SALES            RELAYED_NOT_CAPTURED
BLOCK_TRADE_DATA_PAGE_CAPTURED         NO
ROW_LEVEL_MATCH_TO_TIME_SALES_POSSIBLE NOT_IDENTIFIED
```

The conservative consequence is identical either way: **an unflagged tape's
volume is an UPPER BOUND on CLOB volume, never equal to it.**

**Three-way classification, and UNKNOWN is never silently CLOB.** The
documented tape has four columns and no trade type, so an unflagged row means
"we do not know". Defaulting it to CLOB would undo this whole correction in one
line, so the only route from UNKNOWN to CLOB is a real block publication in
hand: a row absent from an index that EXISTS is positive evidence of a CLOB
execution; a row absent from an index that does not exist is not.

Updating this broke six existing tests, which is the fix working: those tests
fed raw unflagged rows and had been getting fills out of them.

**The shared precondition, now stated once and applied to all three models:**

```
clob_after_entry = CLOB volume at our price, after entry, > 0
```

This keeps C-11 intact — cancellation can advance a queue position, it cannot
create a fill — and adds that a block cannot either.

**The symbol-day gate blocks rather than annotates.** `VERDICT` is
`USABLE` / `CONTAMINATED_BY_BLOCKS` / `UNRECONCILED_VOLUME_MISMATCH` /
`UNRECONCILED_NO_DMR_ROW`, and only the first permits queue inference. A
missing DMR row is not a passed control.

**The reconciliation arithmetic is deliberately not computed for blocky days.**
Whether the DMR's `Trade Volume` ("Total traded volume for the day") INCLUDES
`Block Volume` ("Volume from block trades") decides the sum, and the captured
page does not say. `DMR_TRADE_VOLUME_INCLUDES_BLOCK_VOLUME = NOT_IDENTIFIED`.
Guessing would produce a check that always passes or one that always fails, and
both look like evidence.

**Publication times differ**, so the join key is the business date and never a
clock reading: the tape updates ~18:00 ET and the Daily Market Report ~00:00 ET
(relayed), on a business day that already ends at 17:00 ET (captured).

```
JOIN_KEY      BUSINESS_DATE
JOIN_KEY_IS_NOT  UTC_CALENDAR_DATE, FILE_DISCOVERY_TIMESTAMP, FETCH_TIMESTAMP
```

**Nothing is promotable.** `COUNTERFACTUAL_RESULTS_PROMOTABLE = False`, with
`PROMOTION_REQUIRES` naming the three conditions, so the promotion step has a
flag to check rather than a paragraph to remember.

The block landing page is fetched as a CANDIDATE — its path is in none of our
captured pages — and **a 404 there is a finding, not a failure**: it would mean
row-level exclusion is unavailable and every symbol-day carrying block volume
stays blocked for queue inference indefinitely.
