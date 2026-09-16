# Run 79 — the latency / edge-decay evidence gate: findings

Job 103459038856, `psql exit=0`, all nine statements, sha256
`c73308e5a1aecc1f7a1364d0ea38e2ee918ac11a6333f8227995e5d1ef253130`,
2026-09-11 23:50–23:55Z. `mirror_live=false`. **No economic figure was
computed.** Run 80 is not started.

---

## 0. The evidence gate (18 fields)

| field | stage 2 | populated | universe | % | span | stage 5 |
|---|---|---|---|---|---|---|
| `trades.ts` | WRITE_SITE | 962,454 | 962,454 | 100.000 | 2025-07-09 → 2026-09-11 | ELIGIBLE **ONLY SPLIT BY SOURCE** |
| `trades.detected_at` | WRITE_SITE | 962,454 | 962,454 | 100.000 | 2026-07-22 → 2026-09-11 | ELIGIBLE |
| `trades.venue_seen_at` | WRITE_SITE | 131,376 | 962,454 | **13.650** | 2026-08-27 → 2026-09-11 | INELIGIBLE — semantics unverified |
| `copy_probes.probe_at` | WRITE_SITE | 215,804 | 215,804 | 100.000 | 2026-08-05 → 2026-09-11 | ELIGIBLE **AS A LOWER BOUND ONLY** |
| `copy_probes.fill_ts` | WRITE_SITE | 215,804 | 215,804 | 100.000 | same | ELIGIBLE |
| `copy_probes.reaction_s` | WRITE_SITE | 215,804 | 215,804 | 100.000 | same | **INTERVAL ONLY — CLOCK_UNRESOLVED** |
| `copy_probes.best_ask` | WRITE_SITE | 214,654 | 215,804 | 99.467 | same | ELIGIBLE (partial) |
| `copy_probes.depth` | WRITE_SITE | 215,804 | 215,804 | 100.000 | same | ELIGIBLE **UP TO THE RETAINED DEPTH ONLY** |
| `copy_probes.book_ok` | WRITE_SITE | 214,654 | 215,804 | 99.467 | same | ELIGIBLE (partial) |
| `price_path.t_s` / `.ask` | WRITE_SITE | 5,734 | 5,734 | 100.000 | **2026-09-02 05:05 → 09-04 23:15** | ELIGIBLE |
| `price_path.sampled_at` | **DB_DEFAULT** | 5,734 | 5,734 | 100.000 | same | ELIGIBLE |
| `mirror_orders.placed_at` | WRITE_SITE | 11,183 | 11,183 | 100.000 | 09-06 → 09-10 | **PRE-SEND CLOCK ONLY** |
| `mirror_orders.done_at` | WRITE_SITE | 11,182 | 11,183 | 99.991 | same | ELIGIBLE (partial) |
| `mirror_orders.updated_at` | WRITE_SITE | 11,183 | 11,183 | 100.000 | 09-06 → 09-11 | INELIGIBLE — mutable |
| `mirror_orders.ask_at_send` | WRITE_SITE | 2,530 | 11,183 | **22.624** | 09-06 → 09-10 | ELIGIBLE (partial) |
| `mirror_orders.receipt` | WRITE_SITE | 10,898 | 11,183 | 97.451 | same | INELIGIBLE — semantics unverified |
| `service_heartbeats.beat_at` | WRITE_SITE | 17 | 17 | 100.000 | 08-28 → 09-11 | ELIGIBLE |

`price_path.sampled_at` returns **DB_DEFAULT / 100% populated**, confirming the
false-UNAVAILABLE the name rule would have produced. The three Run 78
NO_WRITE_SITE fields were not re-tested here and are unchanged.

---

## 1. THE C1 COLUMN HAS FOUR LANES, NOT TWO

This is the run's largest finding and it invalidates part of the design.

| lane | events | p10 | **p50** | p90 | max | negative-lag rows | venue_seen |
|---|---|---|---|---|---|---|---|
| `backfill` | 512,329 | 18,070,575.7 s | **20,831,882.5 s (≈241 days)** | 25,286,972.7 s | 32,696,678.1 s | 1 | 0 |
| `chain` | 197,417 | −1.166 s | **−0.706 s** | −0.114 s | 65,454.9 s | **180,958 (91.7%)** | 97,797 |
| `poll` | 251,113 | 133.908 s | **+306.388 s** | 494.090 s | 18,538.2 s | 1 | 31,993 |
| `s1` | 1,597 | 3.558 s | **+4.159 s** | 4.816 s | 40.639 s | **0** | 1,586 |

Read one lane at a time, because they are four different measurements:

- **`backfill` is not a detection at all.** A median lag of ~241 days is history
  being loaded, and `detected_at` records when we ingested the archive. It can
  never appear in a latency estimate.
- **`chain` is clock-corrupted at scale.** 91.7% of rows have `detected_at`
  BEFORE `ts`, tightly clustered at −0.7 s. That is not a fast reaction; it is
  proof the block timestamp and the app clock are not comparable. The earlier
  "−0.73 s median" was not a small sample artifact — it is 180,958 rows.
- **`poll` measures the polling cadence**, not reaction: a 134–494 s
  interdecile band is the sweep interval.
- **`s1` is the only lane whose lag is positive, tight and physically
  plausible** — 3.2 s min, 3.6–4.8 s interdecile, **zero negative rows** on
  1,597 events. It is the only C1→C2 measurement in retained data that is not
  obviously an artifact.

Even `s1` remains **CLOCK_UNRESOLVED as an absolute**: it still crosses C1→C2.
What it gives is a *shape* — small, positive, low-variance — that the other
three lanes do not.

### DEFECT IN MY OWN DESIGN, caught by this run

§4 defined `U1` as `source IN ('chain','poll')`. There are four lanes, and that
filter **excluded `s1` — the single most usable lane in the table** — while
admitting `poll`, whose lag is a polling interval. `U1 = 448,533 = 197,417 +
251,113 + 3`; the 1,597 `s1` rows are not in it. The `U1` definition must be
rewritten by lane semantics, not by a guessed literal list, before run 80.

---

## 2. NO VENUE-SIDE (C4) TIMESTAMP IS RETAINED — but three of OUR OWN stamps are

37 distinct top-level keys across 10,898 `mirror_orders.receipt` bodies and
8,789 `live_orders.raw` bodies (19,687 witnesses). **None is a venue
timestamp.** Components E (submit→ack) and F (ack→fill) stay NOT IDENTIFIABLE
from the venue's own clock.

### A SECOND DEFECT IN MY OWN PROBE

`live_orders.raw` carries **`t_detect`, `t_send`, `t_reply` on 925 rows** — and
my classifier regex `(time|ts|stamp|_at$|date|created|epoch|clock)` labelled all
three **"not time-shaped."** They are exactly the stamps that bracket
decision → submit → reply. The classifier produced a false negative on the most
relevant keys in the table.

They survived only because the statement prints the **full key census** rather
than the classifier's verdict alone. A probe that had reported only its own
classification would have concluded "no timing keys" and been wrong.

What they are, stated carefully: these are **C2 stamps written by us**, not C4
stamps from the venue. `t_reply − t_send` is a client-observed round trip on one
process's clock — a same-domain difference, therefore a legitimate point
estimate — covering **925 rows of the pre-mirror entry-sleeve lane only**.
Component E moves from NOT IDENTIFIABLE to **PARTIALLY IDENTIFIABLE, on a
925-row non-mirror cohort**, and their semantics are unverified until the write
site is read.

(`mirror_orders.receipt` also carries a bare `at` key on 7 rows — likewise
missed by the regex, and negligible.)

---

## 3. A C2↔C3 BRIDGE EXISTS — AND IS UNDERPOWERED

16 candidate keys examined, **2 parsable app stamps, 2 services qualifying**.
Skew (DB stamp − app stamp): min **0.115 s**, p50 **0.375 s**, max **0.634 s**.

Verdict as printed: BRIDGE FOUND. Verdict as it should be **used**:

- **2 witnesses is not a distribution.** Under the standing discipline this is
  one step above NOT TESTED, and it may not be quoted as "the app/DB skew."
- Both stamps are from **current** heartbeats (17 rows total). A skew measured
  tonight says nothing about the app-vs-DB skew during the August–September
  period the economics will cover.
- It constrains **C2↔C3 only**. The chain lane's −0.706 s is a **C1↔C2**
  question, and this bridge cannot settle it. Sub-second skew of ~0.4 s between
  our own two clocks does not explain a −0.7 s median against block time.

---

## 4. POPULATION ATTRITION

| universe | events | conditions | notional | dropped next | $ dropped next |
|---|---|---|---|---|---|
| U0 RN1 fills retained | 962,459 | 46,303 | $157,583,367.52 | 513,926 | $58,272,344.24 |
| U1 + defensible source clock | 448,533 | 29,200 | $99,311,023.28 | 235,470 | $52,803,208.59 |
| U2 + exact fill-level book | 213,063 | 17,751 | $46,507,814.69 | 123,163 | $24,804,343.93 |
| U3 + PMUS-mappable (ever, coarse) | 89,900 | 7,188 | $21,703,470.76 | 26,121 | $7,289,128.74 |
| U4 + settlement known | 63,779 | 4,526 | $14,414,342.02 | — | — |
| **cohort U2 ∩ U4** | **112,956** | **9,506** | **$23,930,620.28** | — | — |

The U0→U1 drop of 513,926 events / $58.3M is **almost entirely the `backfill`
lane** (512,329 rows), which is correct to drop — but it is dropped for the
wrong reason (a literal list) and it took `s1` with it. Recompute before run 80.

U0 reads 962,459 here and 962,454 in statement 0: five RN1 rows arrived during
the five-minute run. The ingestion path is live even though **trading is
paused** — `copy_probes` was still being written at 23:49Z. Any later run must
pin a cutoff rather than compare two live reads.

### The inherited selections, measured

| side | fills | notional | with a probe | with a book | errored | reaction p50 | reaction max |
|---|---|---|---|---|---|---|---|
| BUY | 962,403 | $157,519,652.45 | 215,817 | 214,667 | 3 | **−0.665 s** | 127.189 s |
| SELL | **64** | $67,230.44 | **0** | **0** | 0 | — | **NOT TESTED** |

- The **BUY-only selection costs almost nothing by count** — RN1's retained flow
  is 99.993% BUY. This is *better* than the design warned. It is **not**
  evidence that he rarely exits: on this venue model an exit is a complement
  BUY, so exits are inside the BUY count, not the SELL count.
- The SELL row is **NOT TESTED**, not "no slippage on sells."
- **Probe coverage of U0 is 22.4%** (215,804 of 962,454).
- `reaction_max` is **127.189 s** against a documented `MAX_REACTION_S = 120`.
  The gate is applied to a `latency_s` computed in the ingestion payload, which
  is not the same quantity as `reaction_s` written on the row. Small, but it
  means the 120 s cut is not exactly the cut on this column — to be named before
  the cohort is used.

---

## 5. WITNESS LEDGER — nothing vacuous

| test | witnesses | verdict |
|---|---|---|
| statement 0: evidence-gate fields measured | 18 | TESTED |
| statement 2: RN1 fills with a source clock | 962,471 | TESTED |
| statement 3: JSON bodies probed for a venue stamp | 19,687 | TESTED |
| statement 4: heartbeat rows with a JSON detail | 17 | TESTED |
| statement 5: RN1 fills in U0 | 962,471 | TESTED |
| statement 5b: RN1 fills carrying a probe row | 215,821 | TESTED |

Every test fired against a non-empty population. No result in this run is a
filter that could not fire.

---

## 6. THE COVERAGE WALL FOR THE EDGE-DECAY CURVE

`price_path` — the only retained post-event price series, and the whole
instrument for Phase 6 — holds **5,734 rows spanning 2026-09-02 05:05 to
2026-09-04 23:15**.

The mirror's own books begin **2026-09-06 00:40**. The two do not overlap by a
single row.

So the delay-bucket curve, where it can be built at all, is built on the
**pre-mirror entry-sleeve lane over a 2.75-day window**, and says nothing
directly about the mirror era. Combined with the fixed offsets
`(0, 30, 60, 120, 281, 600)` s, the requested `+1 / +2 / +5 / +10 s` and
`+300 s` buckets remain **NOT IDENTIFIABLE**, and now so does *any* bucket for
the period BETTOR actually traded.

---

## What run 79 changes about the design

1. **`U1` must be redefined by lane semantics**, not a literal source list;
   `s1` belongs in, `backfill` belongs out, and `poll` belongs in only as a
   cadence-limited lane clearly labelled as such.
2. **Component E (submit→ack) is upgraded to PARTIALLY IDENTIFIABLE** on the
   925 `live_orders.raw` rows carrying `t_send`/`t_reply` — same-domain, so a
   point estimate — after the write site is read and the semantics verified.
   It remains NOT IDENTIFIABLE for the mirror lane.
3. **The C2↔C3 bridge is recorded as FOUND BUT UNDERPOWERED (n=2, present-day
   only)** and may not be used to repair historical clocks.
4. **The `chain` lane's −0.706 s is now a measured, 180,958-row fact**, not an
   anecdote, and it is a C1↔C2 artifact the bridge cannot resolve.
5. **Phase 6's instrument does not reach the mirror era**, which must be stated
   before any decay curve is presented.

`mirror_live=false`. Run 80 not started; these findings go back for review
first.
