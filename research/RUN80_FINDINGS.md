# Run 80 — population and provenance for the two estimators: findings

Job 103463476676, `psql exit=0`, eleven statements, sha256
`90817a5deab6cb9cac7d731284e7e1dbf7f39bf9afd3548a8d1fd5cd003d6156`,
2026-09-12 00:16–00:24Z. **No economics: no drag figure, no latency figure.**
Run 81 not started. `ai_trades` untouched. `mirror_live=false`.

`AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z`.

---

## 0. THE CUTOFF HELD — proven, not assumed

| | |
|---|---|
| RN1 rows all time (at read) | **962,670** |
| **U0 at the cutoff** | **962,509** |
| U0 notional | **$157,600,711.33** |
| **U0 re-read at end of run (statement 9)** | **962,509** |

**Zero drift.** 161 rows arrived after the cutoff during the run and were
correctly excluded, so the pin is doing real work rather than being decorative.
Run 79's five-row drift cannot recur.

---

## 1. LANE CENSUS — four lanes, no HALT

| lane | events | notional | first | last | eligibility |
|---|---|---|---|---|---|
| `backfill` | 512,329 | $56,459,968.66 | 2025-07-09 | 2026-07-22 | A: diagnostic ALONE · B: EXCLUDED |
| `poll` | 251,116 | $56,610,613.84 | 2026-07-24 | 2026-09-11 | A: online · B: cadence only |
| `chain` | 197,467 | $42,717,753.25 | 2026-08-10 | 2026-09-11 | A: online · B: EXCLUDED |
| `s1` | 1,597 | $1,812,375.58 | 2026-08-29 | 2026-09-11 | A: online · B: shape only |

No unknown lane. **No HALT row fired.**

Note the lane date ranges: `backfill` ends 2026-07-22, exactly where `chain`
and `poll` begin. The lanes are near-disjoint in time, not interleaved.

---

## 2. U2 — THE PROBE LINKAGE IS THE ONLY REAL FILTER

| predicate | events | conditions | notional | events lost | $ lost |
|---|---|---|---|---|---|
| U0 at the cutoff | 962,509 | 46,309 | $157,600,711.33 | — | — |
| + exact `trade_id` linkage | 215,858 | 17,963 | $48,724,247.28 | **746,651** | **$108,876,464.05** |
| + `book_ok` | 214,708 | 17,775 | $48,336,268.86 | 1,150 | $387,978.42 |
| + `error IS NULL` | 214,708 | 17,775 | $48,336,268.86 | **0** | $0.00 |
| + price valid | 214,708 | 17,775 | $48,336,268.86 | **0** | $0.00 |
| + depth present | 214,708 | 17,775 | $48,336,268.86 | **0** | $0.00 |
| **= U2** | **214,708** | **17,775** | **$48,336,268.86** | 0 | $0.00 |

**U2 = 22.31% of U0 by event, 30.67% by notional.** One predicate does
essentially all the work: whether a probe exists at all. The four quality
predicates after `book_ok` remove **nothing** — a probe row that exists is a
usable probe row.

Cross-check against run 79: U2 was 213,063 under the withdrawn `U1` filter and
is 214,708 now. The difference is the 1,596 `s1` rows the old source list threw
away, plus 49. **The defect run 79 found is confirmed fixed by the arithmetic.**

---

## 3. THE BACKFILL FENCE IS MOOT IN THE DATA — and was still right to build

| stratum | events | conditions | notional | shares |
|---|---|---|---|---|
| `A_CHAIN` | 195,648 | 15,397 | $42,097,682.56 | 87,358,470 |
| `A_POLL` | 17,464 | 4,071 | $4,426,905.04 | 9,062,040 |
| `A_S1` | 1,596 | 754 | $1,811,681.26 | 3,422,232 |
| **`A_ONLINE_COMBINED`** | **214,708** | **17,775** | **$48,336,268.86** | **99,842,743** |
| **`A_BACKFILL_DIAGNOSTIC`** | **0** | 0 | — | **NOT TESTED — no rows** |

**Not one backfill row carries an exact probe.** The probe fires on the live
ingestion fan-out; an archive load never triggers it. So the contamination the
fence was built to prevent does not exist in retained data.

The fence stays. It is now a **measured** zero rather than an assumed one, and
it prints `NOT TESTED — no rows in this stratum`, not a silent blank.

### The composition finding that matters more

    A_CHAIN  91.12%   A_POLL  8.13%   A_S1  0.74%   of U2 by event

Per-lane probe coverage explains it:

| lane | in U0 | in U2 | coverage |
|---|---|---|---|
| `chain` | 197,467 | 195,648 | **99.08%** |
| `poll` | 251,116 | 17,464 | **6.95%** |
| `s1` | 1,597 | 1,596 | **99.94%** |

`poll`'s median detection lag is ~306 s, and the probe's own 120 s gate rejects
most of it — so **the observation set is 91% the lane whose clock is unusable,
and the one lane that can be timed is 0.74% of it.**

For Estimator A this is harmless: A needs no clock. **For the audit as a whole
it is the central structural fact — A and B cannot be joined on a common
population.** Any sentence of the form "the drag was caused by N seconds of
latency" is unsupportable on this data, because the rows that carry the drag and
the rows that carry a usable duration are almost disjoint sets.

---

## 4. DEPTH — THE RETAINED BOOK CAN PRICE ALMOST EVERYTHING AT THE 10% SIZE

`A_ONLINE_COMBINED`, 214,708 events:

| | |
|---|---|
| average levels retained | **7.67** (min 1, max 8) |
| at the 8-level cap | **193,329 (90.04%)** |
| depth $ available, p50 | **$6,370.99** |
| `DEPTH_EXHAUSTED` at `q_a` (10% of his notional) | **308 — 0.14%** |
| `DEPTH_EXHAUSTED` at `q_b` (his full shares) | **2,888 — 1.35%** |
| `DEPTH_EXHAUSTED` at `q_c` ($1,000 clip) | **28,511 — 13.28%** |

`q_a` exhausts far less often than `q_c` because 10% of a median ~$225 fill is
~$22, well under a flat $1,000.

**Read the 90% cap correctly:** nine in ten probes retained exactly eight
levels, which means the true book was *deeper* than what we kept. So these
exhaustion rates are **upper bounds on unpriceability**, and every
`DEPTH_EXHAUSTED` row means *unknown beyond observed depth* — never an
extrapolated fill.

**Consequence for run 81:** at `q_a`, Estimator A can honestly price **99.86%**
of U2. The size question is far less binding than the design feared.

---

## 5. THE 120 s GATE — the code reading confirmed, and quantified

| lane | events | reaction > 120 s | % | $ over 120 | gate p50 | **clamped to 0** | reaction p50 | dispatch p50 | dispatch p90 |
|---|---|---|---|---|---|---|---|---|---|
| `chain` | 195,648 | **0** | 0.000 | — | 0.000 | **180,066** | −0.696 | −0.696 | −0.109 |
| `poll` | 17,464 | **60** | 0.344 | **$2,854.24** | 80.591 | 1 | 80.787 | **0.017** | **0.403** |
| `s1` | 1,596 | **0** | 0.000 | — | 4.159 | 0 | 4.171 | **0.008** | **0.021** |

- **The clamp is real and enormous: 180,066 of 195,648 chain rows (92.0%)
  present `0.000` to the gate.** The gate cannot reject a negative-lag row.
- **The whole 120 s "discrepancy" is 60 poll rows — 0.028% of U2, $2,854.24.**
  Named, quantified, and not a gate violation.
- **The detection→probe dispatch interval is a genuine C2→C2 point estimate:
  8 ms median on `s1`, 17 ms median / 403 ms p90 on `poll`.** Our own internal
  handoff is not where time goes.
- `chain`'s "dispatch" column reads −0.696 s and is **meaningless**: it is
  `reaction_s − max(lag, 0)` where `reaction_s` is itself negative. Dispatch is
  only interpretable where the gate variable is unclamped, i.e. `poll` and `s1`.
  I am naming that rather than letting the number stand.

---

## 6. `t_send` / `t_reply` — THE POPULATION GATE FAILS, DECISIVELY

| lane | whale | rows w/ raw | **with `t_send`** | span | RTT p50 | RTT p90 | verdict |
|---|---|---|---|---|---|---|---|
| `ioc` | rn1 | 964 | **924** | 09-02 11:54 → 09-04 23:05 | **0.320 s** | **0.448 s** | NON-MIRROR |
| `ioc` | 0x076daa87 | 285 | 222 | 09-02 → 09-03 | 0.320 s | 0.434 s | NON-MIRROR |
| `ioc` | ferrarichampions2026 | 161 | 141 | 09-02 → 09-03 | 0.327 s | 0.416 s | NON-MIRROR |
| `ioc` | homerunhazard | 87 | 83 | 09-02 → 09-04 | 0.330 s | 0.411 s | NON-MIRROR |
| `rest` | rn1 | 8 | 1 | 09-02 | 0.347 s | 0.347 s | NON-MIRROR |
| **`mirror`** | **rn1** | **1,631** | **0** | — | — | — | **NOT TESTED** |
| `(null)` | rn1 | 6,186 | 0 | — | — | — | NOT TESTED |

**The mirror lane has 1,631 JSON bodies and not one timing stamp.** Every timed
row is lane `ioc` or `rest` — and the entire timed span is
**2026-09-02 → 09-04, which ends before the mirror's first book on 09-06.**

So the cohort is **doubly disjoint from the mirror**: wrong lane *and* wrong
period. Per the standing rule it is an **ENGINEERING DIAGNOSTIC / SENSITIVITY
ONLY** and is not promoted to the primary mirror estimator.

What it does establish, on the entry sleeve in early September: **venue round
trip ≈ 0.32 s median, 0.45 s p90**, stable across four different whales — a
same-process C2→C2 point estimate, the only `POINT_IDENTIFIED` duration this
audit has found.

---

## 7. A MIRROR-PERIOD SERIES EXISTS — top-of-book only, and lopsided

Mirror window 2026-09-06 00:40 → cutoff:

| | |
|---|---|
| conditions observed | **5,159** |
| total shadow ticks | 206,021 |
| ticks carrying a quote | **149,655** |
| conditions with ≥ 2 quotes | **1,986 (38.5%)** |
| conditions with ≥ 10 quotes | **1,751 (33.9%)** |
| quotes per condition, **p50** | **0.0** |
| observed span, p50 | 8,155.6 s (2.27 h) |

**Verdict as printed:** a repeated top-of-book series exists — **top-of-book
shape only, NO DEPTH** — and pre-mirror `price_path` may not substitute.

So `MIRROR_PERIOD_TIME_DECAY_CURVE` is **not** "NOT IDENTIFIABLE". But two
limits are load-bearing and must travel with any future curve:

1. **No depth.** A shadow tick carries a quote, not a book, so it can support a
   top-of-book decay *shape* and can never produce a depth-walked executable
   price. It is not the same instrument as `copy_probes`.
2. **The median condition has zero quotes.** Coverage is concentrated in ~39% of
   conditions; a curve built here describes the well-observed subset, and the
   selection into that subset is itself unmeasured.

---

## 8. SETTLEMENT SELECTION — roughly half, and nearly profile-neutral

`A_ONLINE_COMBINED`:

| | U2 | `EVENT_RESOLVED_BY_CUTOFF` | retention |
|---|---|---|---|
| events | 214,708 | **114,047** | **53.12%** |
| notional | $48,336,268.86 | **$24,986,819.60** | **51.69%** |
| price p50 | 0.4700 | 0.4800 | — |
| size p50 | $16.81 | $17.16 | — |
| sports | 9 | **8** | one sport fully unresolved |

The selection halves the cohort but barely moves price or size. **That is not a
licence to generalise back to U2** — it is one dimension of neutrality, and the
missing sport is a named gap to identify in run 81.

### 8b. `resolved_at` provenance — I MUST CORRECT MY OWN PRE-WRITTEN VERDICT

| | |
|---|---|
| resolved conditions RN1 traded | **33,419** |
| **`resolved_at` BEFORE a fill on the same condition** | **1,902 (5.7%)** |
| hours fill → resolve, p50 | 2.71 h |
| **whole-second stamps** | **33,004 of 33,419 (98.76%)** |

**The statement printed a hardcoded literal saying the two `gamma.py` write
branches are "NOT SEPARABLE from retained data". I wrote that string before the
data came back, and the data contradicts it. I am retracting it as an
assertion.**

Postgres `now()` carries microsecond precision; a venue `closedTime` / `endDate`
parsed from ISO is almost always whole seconds. The measured split —
**415 sub-second stamps vs 33,004 whole-second** — is therefore a **candidate
discriminator**: the 415 are very likely the `now()` branch (our fetch clock),
the 33,004 very likely the venue branch. A `now()` landing exactly on a whole
second has probability ~10⁻⁶, so the inference is strong in one direction and
merely probable in the other (the venue *could* emit sub-second times).

**This is evidence, not proof.** It does not create a
`SETTLEMENT_KNOWN_TO_BETTOR` cohort — the venue branch still tells us only when
the event resolved. What it does say is that the `now()` branch is ~1.2% of
rows, so `EVENT_RESOLVED_BY_CUTOFF` is overwhelmingly the **venue's** clock, and
should be described that way.

**Separately: 1,902 conditions (5.7%) carry `resolved_at` EARLIER than a fill we
recorded on that condition.** That is an impossibility under any clean reading —
either the venue's resolution stamp, or our fill stamps, or the
condition-to-market join is wrong on those rows. It is a data-integrity finding
in its own right and is carried forward as an open item, not explained away.

---

## 9. WITNESS LEDGER — nothing vacuous

| test | witnesses | verdict |
|---|---|---|
| statement 1: U0 rows censused by lane | 962,509 | TESTED |
| statements 2/3/4/5/8: U2 rows built | 214,708 | TESTED |
| statement 6: `live_orders` rows with a JSON body | 13,848 | TESTED |
| statement 7: mirror-window shadow ticks | 206,021 | TESTED |
| statement 8b: resolved conditions RN1 traded | 33,419 | TESTED |
| statement 9: U0 re-read (must equal statement 0) | **962,509** | TESTED — **matches** |

---

## What run 80 settles, and what it opens

**Settled:**

1. The cutoff works; drift is zero.
2. `A_BACKFILL_DIAGNOSTIC` is empty — the fence is correct and moot.
3. U2 is 214,708 / 17,775 conditions / $48.34M, and the probe's existence is the
   only binding filter.
4. At `q_a`, 99.86% of U2 is priceable from retained depth.
5. The 120 s discrepancy is 60 poll rows and $2,854.24.
6. `t_send`/`t_reply` are non-mirror **and** pre-mirror — diagnostic only. Venue
   RTT ≈ 0.32 s p50 / 0.45 s p90 on the entry sleeve.
7. A mirror-period top-of-book series exists, with no depth and a zero median.

**Opened:**

8. **U2 is 91% `chain`** — the lane whose clock is unusable — while `s1`, the
   only timeable lane, is 0.74%. Estimator A and Estimator B are effectively
   disjoint populations and must never be narrated as cause and effect.
9. **1,902 conditions resolve before a fill we recorded.** Open data-integrity
   item.
10. One sport is entirely absent from the resolved cohort; name it before any
    settlement-dependent figure is quoted.

`mirror_live=false`. Run 81 not started; these findings go back for review.
