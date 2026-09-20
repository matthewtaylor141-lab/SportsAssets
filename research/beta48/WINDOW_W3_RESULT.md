# W3 — rotation + early eligibility, measured

Window **2026-09-20T23:03:17Z → 23:33:17Z**, declared 22:40Z before the
deploy; bounds filled 22:58Z from the deploy record (workers `0868e3e`
live 22:53:17Z + the same 10-minute warm-up W1 and W2 used).

**Provenance.** 25 ticks, **one** pacing version
(`BETTOR_CAPTURE_PACING_V3_ROTATED_FOLLOWUPS`). Cohort 49 observations.
Query `research/bettor_window_w3.sql`, research-sql run 179 at
23:38:25Z. Preflight (run 178) confirmed migration 091 applied and the
tick ledger live before the clock started.

---

## PART 1 — Window close, with pending outcomes marked

### Initial reads

| | W1 | W2 | **W3** |
|---|---:|---:|---:|
| scheduled | 148 | 116 | **118** |
| attempted | 66 | 41 | **49** |
| skipped for budget | 82 | 75 | **69** |
| coverage | 44.6% | 35.3% | **41.5%** |
| rate-limited (429) | 14 | 5 | **7** |
| readable | 25 | 22 | **27** |
| unreadable-other | 27 | 14 | **15** |
| pacing range | — | 1.0..5.062 | **1.0..5.695** |

### The rotation, verified directly rather than inferred

| head horizon | ticks |
|---:|---:|
| 60 | 6 |
| 300 | 7 |
| 900 | 6 |
| 3600 | 6 |

All four lead, near-evenly, across 25 ticks. No tick recorded
`NO_FOLLOWUP_BUDGET`. **This is the first direct evidence the rotation
cycles** — W1 and W2 could not have shown it, because the column did
not exist.

### Cohort outcomes (49 observations) — **provisional, read at 23:38Z**

| horizon | not yet due | eligible | attempts | on time | late | **pending** | missing |
|--------:|---:|---:|---:|---:|---:|---:|---:|
| 60 | 0 | 49 | 14 | **0** | 14 | **7** | 28 |
| 300 | 0 | 49 | 11 | **0** | 11 | **10** | 28 |
| 900 | 11 | 38 | 7 | **0** | 7 | **14** | 17 |
| 3600 | **49** | 0 | 0 | — | — | 0 | 0 |

Every row sums to 49. **Pending is not missing**: 31 outcomes had
recovery deadlines still ahead when this was read, and the 3600s row is
entirely NOT_YET_DUE. Those numbers will move. They are re-read in
Part 2.

### Timing placement — every single read

| horizon | in band | missed by ≤90s | **missed by >90s** |
|--------:|---:|---:|---:|
| 60 | 0 | 0 | **14** |
| 300 | 0 | 0 | **11** |
| 900 | 0 | 0 | **7** |

Not one cohort read landed inside its tolerance band, on either side,
and none even landed within 90s of it.

### Window activity is not cohort outcome

| | |
|---|---:|
| follow-ups attempted in window ticks | 59 |
| …belonging to the W3 cohort | 32 |
| …belonging to **older** observations | 42 |

Older observations served in-window, by horizon — all LATE_RECOVERY:
60s **9**, 300s **7**, 900s **10**, **3600s 16**.

The 3600s horizon **was** served during the window, 16 times. Every one
of those reads concerns an observation made before the window: W3's own
cohort is at most 30 minutes old at close, so no cohort observation is
3600s-due. Those 16 reads are evidence the rotation reaches 3600s. They
are **not** cohort outcomes and are not used to pass any criterion.

### Demand against capacity

| | W2 | **W3** |
|---|---:|---:|
| FU_DUE (true outstanding, summed over ticks) | 190 | **1010** |
| FU_SELECTED | 64 | 59 |
| FU_ATTEMPTED | 64 | 59 |
| FU_ON_TIME | 13 | **0** |
| FU_LATE | 51 | 59 |

Roughly 40 outstanding eligible reads per tick against ~2.4 attempted —
a **17× shortfall**, against W2's 3×.

---

## Against the declared criteria, unrevised

| criterion | result |
|---|---|
| `COVERAGE_EVERY_HORIZON` — attempts > 0 at every horizon with eligible cohort demand | **PASS** for 60s (14), 300s (11), 900s (7). **3600s not yet evaluable** — 0 eligible, 49 not-yet-due. Deferred to Part 2, not scored as failure. |
| `ON_TIME_300S` — ≥ 40% | **FAIL**, 0/49 = 0% |
| `ON_TIME_900S` — > 0% | **FAIL**, 0/38 = 0% |
| `FAIL_IF` — any horizon with standing eligible demand receives zero attempts | **not triggered** |

## What W3 improves, and what remains inadequate

**Improves service.** The rotation cycles, verified directly. Every
horizon with eligible demand received attempts, including 900s, which
got zero in W2 against 22 eligible observations. 3600s was reached for
the first time. Initial-read coverage also recovered, 35.3% → 41.5%.

**Degrades timing.** On-time reads went **16 → 0**. Missing outcomes at
60s went **0/41 → 28/49**. Every cohort read landed more than 90
seconds past its horizon.

**What this does and does not establish.** Allocation and eligibility
shipped together. The attempts and timing columns diagnose *behaviour*
— they do not isolate each change's *causal contribution*, and I do not
claim they do. The 60s on-time loss is consistent with at least three
non-exclusive explanations: the per-horizon cap cutting 60s attempts
from 41 to 14, the eligibility change, and a backlog that grew 5× per
tick. This window cannot separate them.

**A hypothesis for W4, not a conclusion.** `mids_due` orders
oldest-first. With ~40 outstanding per tick and ~2.4 served, the oldest
eligible observation is always far past its band, so a widened
selectable window may never be reached — every read is a recovery of
something long overdue. In W1 I tested the ordering hypothesis and
**refuted** it, correctly: the queue was not being serviced at all then,
so its order could not matter. It is now serviced, which is exactly the
condition under which ordering could begin to bind. That is a
hypothesis to test, not a finding.

**Inadequate overall.** The collection system is not complete. W3 made
service fair and timing worse; at this demand-to-capacity ratio,
spreading a fixed budget across four horizons gives each too few reads
to hit any band.

---

## A correction: the request-budget floor was **not** restored

I previously said W3 would restore the original floor of 1. **It does
not.** The deployed rule at `0868e3e` is unchanged:

```
total_budget = max(2, int(MAX_READS_PER_TICK * min(1.0, BASE / pacing)))
```

| pacing | unfloored | `max(1,…)` — original | `max(2,…)` — **deployed** |
|---:|---:|---:|---:|
| ≤ 5.0 | 2–10 | same | same |
| ≥ 5.063 | 1 | 1 | **2** |

At maximum backoff the deployed code issues **two reads per tick where
the original issued one** — a 2× increase in request rate precisely
when the venue is refusing. W2's pacing reached 5.062 and W3's reached
**5.695**, so this was live in W3 and the floor did bite. The
regression is still present; W3 did not remove it, and I should not
have said it would.

---

## Scope

Nothing here is evidence about markets, fills, profitability or adverse
selection. No settlement term appears in any of these queries. Objects
A, B, C and D stay separate; B, C and D remain NOT_IDENTIFIED.
`mirror_live=false`, COMMAND undeployed, real-order activity NONE, real
capital at risk 0.
