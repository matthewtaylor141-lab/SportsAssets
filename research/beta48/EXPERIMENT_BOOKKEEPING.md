# EXPERIMENT BOOKKEEPING — reconciliations before the record is permanent

Two numbers drifted across reports and one preregistered constant was silently
changed. Both are reconciled here from the captured data, not smoothed.

```
STATUS_REPORTING_RULE = RUN_STATUS_MUST_BE_QUERIED_NEVER_INFERRED
```

---

## 1. THE BROAD / ROUTED COUNTS — 9,267 vs 9,185 vs 9,182

All three reproduce exactly from the captured boards. The rule and threshold
were **identical** in all three; only the snapshot and the join differ.

### 9,267

```
COUNT                       9,267
SOURCE_RUN / BOARD_SNAPSHOT run 35048890640 (segment 8), board walked
                            2026-09-16T02:40:54Z .. 02:42:34Z
RULE VERSION                eligibility.stage1, MAX_SPREAD_TICKS_BROAD = 5
OPEN_MARKET_COUNT           19,999 / 20,000
TWO_SIDED_COUNT             12,313
SPREAD_THRESHOLD            <= 5 ticks
WHY_THIS_DIFFERS            It is a DIFFERENT WALL-CLOCK BOARD, 9 h 26 min
                            earlier than the others. 1,023 more markets carried
                            a two-sided quote at 02:40Z than at 12:07Z.
STATUS                      VALID for its own instant. Not retracted.
```

### 9,185  — CANONICAL for the 12:07Z snapshot

```
COUNT                       9,185
SOURCE_RUN / BOARD_SNAPSHOT run 35093823242 (segment 10), board.json
                            captured 2026-09-16T12:07:13Z
RULE VERSION                eligibility.stage1, MAX_SPREAD_TICKS_BROAD = 5
OPEN_MARKET_COUNT           19,999 / 20,000
TWO_SIDED_COUNT             11,290
SPREAD_THRESHOLD            <= 5 ticks
WHY_THIS_DIFFERS            Self-consistent: every row supplies its own status
                            AND its own quotes from one capture.
STATUS                      VALID. This is the correct figure for 12:07Z.
```

### 9,182  — **RETRACTED**

```
COUNT                       9,182
SOURCE_RUN / BOARD_SNAPSHOT segment 10 breadth rows (12:07:13Z) with `status`
                            JOINED FROM segment 8's board (02:40:54Z)
RULE VERSION                identical
OPEN_MARKET_COUNT           19,996  <- THE DEFECT
TWO_SIDED_COUNT             11,290
SPREAD_THRESHOLD            <= 5 ticks
WHY_THIS_DIFFERS            A 9-hour-26-minute-stale status join. The breadth
                            writer does not emit `status`, so I joined it from
                            an older board. Three markets that exist at 12:07Z
                            were absent from the 02:40Z board and were therefore
                            scored NOT_OPEN -- a fact about my join, not about
                            the venue.
```

The three lost markets, all created between the two captures, all `BROAD=True`
at 12:07Z with a one-tick spread:

```
astatc-nfl-min-chi-2026-09-20-margin-long-1-6
astatc-nfl-min-chi-2026-09-20-margin-tie
astatc-nfl-min-chi-2026-09-20-margin-short-1-6
```

**9,182 is withdrawn as a defective figure.** It is not a legitimate alternative
snapshot; it is 9,185 minus a join artifact. Every document that carried 9,182
now carries 9,185.

I also wrote, in the funnel output that produced 9,182, that "the two captures
are minutes apart". They are **9 hours 26 minutes** apart. That assertion was
made without checking and is corrected here.

### The canonical field for the census now running

```
CURRENT_CENSUS_STAGE1_BROAD_ROUTED_MARKETS = READ FROM THE RUN'S OWN
                                             census_plan.json
```

It is **not** 9,185. Run 35105863528 enumerates its own board at its own
instant, and its plan file records `STAGE1_BROAD_ROUTED_MARKETS` for that
board. Carrying a number forward from a different snapshot is the exact error
that produced 9,182.

---

## 2. AUDIT_N — 300 FROZEN, 540 RUNNING

```
ORIGINAL_AUDIT_N                 300
                                 Frozen in MAKER_ELIGIBLE_UNIVERSE_V1.md line
                                 691: "fixed on request budget, NOT on observed
                                 miss rate".
REVISED_AUDIT_N                  540
REVISION_TIME                    2026-09-16, at census_collect.py's first write
REVISION_COMMIT                  db661de
RESULTS_OBSERVED_BEFORE_REVISION NO. The first census run died on its first row
                                 write; no audit row has ever been read. No
                                 false-negative rate, no miss rate and no
                                 economics existed at the time of the change or
                                 exist now.
REASON_FOR_REVISION              OVERSIGHT, not design. I parameterized the
                                 audit lane as AUDIT_FRACTION = 0.05 of stage-1
                                 rejects and did not check it against the frozen
                                 absolute. 0.05 x 10,815 rejects = 540.
```

**540 is not the frozen value and is not presented as one.** It is a pre-result
amendment, recorded with its true cause.

**Decision: RATIFY 540, with the reason stated honestly.**

- It is **strictly more** audit evidence than the frozen 300, so it cannot be
  cherry-picking; a larger lane can only sharpen the false-negative estimate.
- The frozen 300 was fixed on **request budget**, not on statistics. 540 costs
  240 extra reads = about 2 minutes at 2 rps, comfortably inside budget.
- The lane is drawn by salted hash over rejects and interleaved by salted hash
  into the scan, so its size does not interact with which rejects are chosen or
  when they are read.
- Reverting to 300 mid-run would discard a scan already in progress in exchange
  for strictly weaker evidence.

What would **not** have been acceptable, and did not happen: changing `AUDIT_N`
after seeing a miss rate, or choosing it to make a result come out. Neither is
possible here — nothing has been read.

---

## 3. THE DECIMAL PIPELINE, BOTH HALVES

```
CANONICAL_NUMERIC_PIPELINE = DECIMAL -> EXACT_DECIMAL_STRING -> DECIMAL
```

The writer half (`_jsonable`) was landed with the crash fix. The **reader half**
is now landed too, because the writer alone is not enough: a reader that leaves
`SPREAD_TICKS` a string invites a lexical comparison, and lexically
**`"10" < "2"`**.

```
from_json_decimal(v)   string -> Decimal, exactly
                       REFUSES a float outright: float(0.1) is not 0.1, and
                       accepting one would make the one exact step lossy
                       "NOT_IDENTIFIED" / None -> None, never 0
rehydrate(row)         one census row, DECIMAL_FIELDS returned to Decimal
read_census(path)      the ONLY supported reader for census.jsonl(.gz)
```

Banned and tested against: lexical string comparison, float coercion, implicit
int conversion. One test asserts the hazard itself — `"10" < "2"` is True while
`Decimal("10") > Decimal("2")` — so nobody has to take the motivation on trust.
Another sorts rehydrated rows and requires `1, 2, 3, 10, 20`.

26 tests in `test_census_collect.py`.

---

## 4. STATUS REPORTING

```
RUN_STATUS_MUST_BE_QUERIED_NEVER_INFERRED = BINDING
```

I reported the first census as IN FLIGHT when it had already failed 18 minutes
earlier. The status was inferred from dispatch time and expected duration
instead of read from the workflow.

Permitted values, taken only from the actual workflow state, read immediately
before any status report:

```
QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
```

Never inferred from dispatch time, expected duration, a scheduled harvest, or
last-known state.

---

## 5. WASTED-BUDGET WORDING

The first run completed the board walk and died before any stage-2 book read.
So the precise statement is:

```
STAGE2_BOOK_READ_BUDGET_WASTED = ZERO
BOARD_WALK_REQUESTS_SPENT      = ~200 (the enumeration did occur)
```

not "no venue budget wasted", which was wrong: board requests were made.

---

## 6. WHAT DOES NOT CHANGE

```
SPRINT_CLOSEOUT_STATUS = RESEARCH / ARCHITECTURE SUBSTANTIALLY COMPLETE
MAKER_ENGINE_GATE_V2   = BLOCKED
BETTOR_V1_BETA_STATUS  = SPECIFIED, SHADOW-ONLY, NOT BUILT
MAKER_PROFITABILITY    = NOT_ESTABLISHED
```

Every item above is bookkeeping or implementation. None of it is an economic
result and none of it moves a gate. The census may update non-UFC trade
recency, depth, queue structure, routing yield and audit miss rate. It cannot
establish actual BETTOR fills, actual BETTOR adverse selection, fair-value edge
or maker profitability.
