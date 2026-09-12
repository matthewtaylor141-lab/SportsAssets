# RUN 80.5 + 80.5b — THE SETTLEMENT DATA-INTEGRITY GATE

Read-only. No economics. No drag figure, no latency figure. `ai_trades` / TRUEEDGE
untouched. `mirror_live=false`. Run 81 not started.

- Run 80.5  — job 103474729116, `psql exit=0`, nine statements, commit `888b55e`.
- Run 80.5b — job 103477342145, `psql exit=0`, six statements, commit `0ab669f`.
  (Two earlier attempts died on my own SQL: run 82 on a missing `FROM`, run 83 on a
  column missing from a `GROUP BY`. Statements that ran before each error are not
  reused here; every figure below comes from the clean run.)

`AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z`, applied per table on that table's
designated field with that field's semantics carried.

---

## 1. THE ANOMALY POPULATION

Conditions where `markets.resolved_at` precedes a retained RN1 fill on the same
condition:

| measure | value |
|---|---|
| conditions | 1,902 |
| post-resolved fills | 50,722 |
| post-resolved notional | $5,452,997.37 |
| share of U0 fills | 5.270% |
| share of U0 notional | 3.460% |
| share of U2 events | 0.789% |
| share of resolved conditions RN1 traded | 1.856% |

## 2. THE EVIDENCE CLASSES

| class | meaning | conditions | fills |
|---|---|---|---|
| A | post-resolved fills arrived on the ARCHIVAL lane (`backfill`) | 1,590 | 45,665 |
| B | post-resolved fills arrived on a LIVE lane | 312 | 2,678 |
| C | LIKELY_FETCH_FALLBACK_TIMESTAMP | **NOT ASSIGNED** | — |
| D | condition↔market join defect | **0** | 0 |
| E | token / price-vector mismatch | **0** | 0 |

**D = 0 and E = 0 are the load-bearing zeros.** They are the reason the timing
anomaly can be discussed at all: the two timestamps are known to describe the
same market. Had D been non-zero those rows would carry no timing conclusion.

**Class C was deliberately not assigned.** It would have required knowing which
branch of `gamma.py`'s `closedTime → closed_time → endDate → end_date_iso`
fallback wrote a given row. The `markets` table carries no schema change in any
migration and no raw venue payload is stored anywhere, so the venue's own
`closedTime`/`endDate` is **NOT RETAINED** and the write branch per row is
unrecoverable. Run 80.5 printed that as a named NOT RETAINED row rather than
inferring the branch.

## 3. RESOLVED_AT_ORIGIN_HEURISTIC (a cross-cutting flag, never a class)

| stamp precision | conditions | anomalous | rate |
|---|---|---|---|
| whole-second `resolved_at` | 33,004 | 1,902 | 5.763% |
| sub-second `resolved_at` | 415 | **0** | 0.000% |

Every anomaly carries a whole-second stamp. No sub-second stamp is anomalous.

## 4. RUN 80.5b — MIDNIGHT × ANOMALOUS, WITH ITS CONTROL

| stamp shape | status | conditions | fills | notional | % of conditions |
|---|---|---|---|---|---|
| has a time of day | ANOMALOUS | 3 | 244 | $18,536.29 | 0.009% |
| has a time of day | clean | 31,173 | 697,503 | $104,276,395.48 | 93.279% |
| exactly midnight UTC | ANOMALOUS | 1,899 | 50,478 | $5,434,461.08 | 5.682% |
| exactly midnight UTC | clean | 344 | 7,677 | $764,337.79 | 1.029% |

The rates:

| measure | value |
|---|---|
| resolved conditions RN1 traded (the frame) | 33,419 |
| anomalous | 1,902 |
| midnight stamps | 2,243 |
| anomalous AND midnight | 1,899 |
| **share of anomalies carrying a midnight stamp** | **99.842%** |
| **anomaly rate WITHIN midnight stamps** | **84.663%** |
| **anomaly rate WITHIN timed stamps** | **0.010%** |
| anomalous with a real time of day (the residual) | 3 |

**The control arm is what makes this mean anything.** 31,176 conditions carry a
real time of day; three of them are anomalous. A midnight rate that ran high
everywhere would have refuted the reading; it does not. The separation is four
orders of magnitude.

Statement 2's own verdict literal was `MIXED` — because the residual is not
zero, the file refused to print a clean explanation. That is correct and is kept.

## 5. THE RESIDUAL, FULLY ENUMERATED

Three conditions, one fixture, all `backfill`, five post-resolved fills, **$30.17
in total**:

| condition | market slug | sport | lane | post fills | post notional | resolved_at | last fill | minutes past |
|---|---|---|---|---|---|---|---|---|
| `0x22699b9db847…` | cs2-ts7-fal2-2025-12-11-game1 | Other-Sports | backfill | 1 | $15.09 | 2025-12-11 18:40:59 | 2025-12-11 23:14:18 | 273.32 |
| `0x6b7c3733c480…` | cs2-ts7-fal2-2025-12-11-game2 | Other-Sports | backfill | 2 | $14.44 | 2025-12-11 20:31:53 | 2025-12-11 23:47:16 | 195.38 |
| `0xc518916d74bd…` | cs2-ts7-fal2-2025-12-11 | Other-Sports | backfill | 2 | $0.64 | 2025-12-11 20:06:27 | 2025-12-11 23:47:18 | 220.85 |

The residual is named, not folded away. It remains **UNRESOLVED**: three
conditions of one esports fixture whose timing the midnight mechanism does not
explain, carrying $30.17 of archival-lane fills.

---

## 6. THE VERDICT — TWO SEPARATE QUESTIONS, NOT ONE

### 6a. TIMESTAMP_INTEGRITY — **UNRESOLVED on the anomalous cohort**

1,902 conditions carry a `resolved_at` that precedes a retained fill on the same
condition. 1,899 of them carry a stamp of exactly midnight UTC, which is the
shape `datetime.fromisoformat("YYYY-MM-DD")` produces from a bare date; the
remaining 3 are unexplained. The write branch per row is not recoverable, so the
anomaly is **not proven** to be the `endDate`-as-midnight defect — it is strongly
consistent with it and with nothing else measured. `resolved_at` is not a
trustworthy settlement instant on this cohort.

### 6b. PAYOUT_VECTOR_INTEGRITY — **NO DEFECT FOUND, in either arm**

| cohort | conditions | prices null | not an array | non-numeric | length ≠ token count | payout sum ≠ 1 | binary w/o unique winner | any defect | rate |
|---|---|---|---|---|---|---|---|---|---|
| ANOMALOUS conditions | 1,902 | 0 | 0 | 0 | 0 | 0 | 0 | **0** | 0.0000% |
| clean resolved conditions (control) | 31,517 | 0 | 0 | 0 | 0 | 0 | 0 | **0** | 0.0000% |

The payout vector was tested on its own terms — structure, length against the
token count, sum, and unique winner on a binary market — with the clean cohort
beside it as the control that makes the comparison mean anything. Both arms are
zero. There is no measured difference between the anomalous conditions and the
rest of the book on payout structure.

### THE REQUIRED STATEMENT, preserved verbatim

> **Settlement timing integrity is unresolved on the excluded cohort; this does
> not establish that the payout vector is incorrect.**

A structurally valid vector is not proof of a *correct* payout — it is proof of a
*well-formed* one. Whether the winning outcome named on these conditions is the
true one cannot be tested from retained data: there is no independent settlement
source stored anywhere. That is a separate UNKNOWN and is carried as one.

---

## 7. CONSERVATIVE_SETTLEMENT_TIMING_QUARANTINE — the exact rule

```
CONSERVATIVE_SETTLEMENT_TIMING_QUARANTINE

  Exclude, from any SETTLEMENT-DEPENDENT measure only, every CONDITION C
  for which, at AUDIT_CUTOFF_TS:

      markets.resolved       IS TRUE
  AND markets.resolved_at    IS NOT NULL
  AND markets.resolved_at   <= AUDIT_CUTOFF_TS
  AND EXISTS a retained RN1 fill F on C with
        F.ts <= AUDIT_CUTOFF_TS
    AND F.detected_at <= AUDIT_CUTOFF_TS
    AND F.ts > markets.resolved_at

  Whole conditions, never individual fills. A condition whose resolution
  stamp is untrustworthy has an untrustworthy stamp for every fill on it.
```

It is a **predicate**, evaluated mechanically. No row is judged individually and
no explanation is required for a row to be quarantined.

**It is a timing quarantine, not a removal of corrupted payouts.** Section 6b is
the reason the distinction is not cosmetic: no payout defect was found on this
cohort. The rule removes conditions whose *clock* we cannot vouch for, and it
must be named that way wherever it appears.

Priced against the settlement cohort:

| | events | notional | share |
|---|---|---|---|
| settlement cohort | 114,009 | $24,980,846.78 | 100% |
| quarantined | 1,694 | $303,303.16 | **1.486%** |
| surviving | 112,315 | $24,677,543.62 | 98.514% |

The cohort and quarantined rows are printed figures from run 80.5b statement 4.
The surviving row is arithmetic on them, not a separately printed figure; run
80.5 printed its own surviving figures (112,316 / $24,677,550.89) against its own
cohort, and the one-event difference is section 8.

209 distinct conditions of the 1,902 carry any probe-backed (U2) event; the rest
of the anomaly population never enters a settlement-dependent measure because it
has no eligible event to begin with.

### The rule that was NOT taken

A mechanism-aware variant — quarantine only the conditions whose `resolved_at`
is *not* a midnight stamp, i.e. only the unexplained residual — was priced beside
it and excludes **0 events / 0.000%** of the cohort, because none of the three
residual conditions carries a U2 event. It is recorded and rejected: it assumes
the midnight mechanism is established, and section 6a says it is not.
**The blunt rule is the conservative one and the blunt rule is what is used.**

---

## 8. A COHORT DRIFT THAT THE CUTOFF DOES NOT PIN

Run 80.5 statement 7 and run 80.5b statement 4 build the settlement cohort from
**textually identical** SQL against the same immutable cutoff. They disagree:

| run | time | cohort events | cohort notional |
|---|---|---|---|
| 80.5 | 01:28Z | 114,010 | $24,980,854.06 |
| 80.5b | 01:46Z | 114,009 | $24,980,846.78 |

One event, $7.28, eighteen minutes apart — 0.0009% of the cohort, immaterial to
every figure above, and **not immaterial as evidence**. Run 80 proved `trades` is
drift-free at the cutoff (U0 re-read identical, zero drift), so the difference
originates in a table the cutoff cannot pin. `markets` is upserted on every
fetch and run 80 established that `markets.updated_at` is overwritten on every
upsert, so a row's `resolved` / `resolved_at` can move under a pinned cutoff.
`copy_probes` is the other candidate.

**Which one is NOT determined from retained output and is not guessed here.**
`EVENT_RESOLVED_BY_CUTOFF` is therefore **not a reproducible population** the way
U0 is, and any settlement-dependent figure must be stamped with the instant its
cohort was drawn. Run 81A opens with a cheap measurement of this.

---

## 9. WHAT IS NOT AFFECTED

**Estimator A is untouched.** `FIRST_RETAINED_OBSERVATION_DRAG` requires no
settlement and no resolution stamp, so no anomaly row is excluded from it. The
quarantine applies to settlement-dependent measures only.

## 10. WITNESS LEDGER (run 80.5b)

| test | witnesses | verdict |
|---|---|---|
| resolved conditions RN1 traded (the frame) | 33,419 | TESTED |
| conditions carrying a NON-midnight `resolved_at` (the control) | 31,176 | TESTED |

Both arms of every comparison above are populated. Nothing in this document rests
on a zero witness count.
