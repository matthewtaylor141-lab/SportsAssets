# Package B — bounded incentive-share measurement (final)

**Not activated.** No orders, no deployment, no probe re-arm. Separate
from the expired observation budget and from the nine remaining
account-read requests. **$0.00 at risk.**

Code: `research/beta48/bettor_incentive_score.py`;
tests `research/beta48/test_bettor_incentive_score.py` — **10 passing**,
including the venue's worked example reproducing 70.6% / 1.9%.

---

## 1. The reward calculation

`100/(100+C)` is gone — a special case needing our order at the best
price, every competitor also there, the side already meeting Target Size
without us, both sides qualifying equally often, and an even pool split.

**Documented rules implemented** (retrieved 2026-09-22T18:14Z):
`Score = discountFactor ^ (ticks from best) × OrderSize`; a random
snapshot each second; Target Size is a **raw aggregate** walked from the
best price outward, orders beyond it scoring nothing; each side
normalised to 1.0 per snapshot **only if** Target Size is met on it;
sides independent; no per-person cap.

**C is competing qualifying *discounted* score, not raw size.** The walk
uses raw size to choose which levels score; the discount applies after.

**Our order changes the book**, computed by scoring it twice:
**E1** it can *create* eligibility; **E2** it can push worse-priced
orders out of the walk, raising our share above any naive ratio.

**A regression I introduced and caught:** the first implementation
walked *orders* rather than *price levels*, so a competitor listed first
exhausted Target Size and excluded us **at our own price** — the exact
same-level-exclusion error the module exists to correct. Two tests pin
it, including one asserting entry order within a level cannot change the
answer.

---

## 2. Simulated is not measured — and the reward estimate is hypothetical too

**Observation can measure market conditions. It cannot establish our
fills.** Nothing in this run produces a measured trading loss, and no
output of it may be described as one.

| quantity | status | why |
|---|---|---|
| book state, Target Size attainment, competing size, uptime | **measured** | read from the venue's own published ladders |
| **hypothetical reward** | **estimated** | our order was never in the book |
| **L_sim, trading loss** | **simulated** | depends entirely on an unvalidated fill model |
| residual inventory | **simulated** | same |

### The reward estimate is not execution-free either

The hypothetical assumes a 100-contract quote **continuously present**
for the whole period. A real quote does not behave that way:

- **A fill removes our order from the book.** It stops scoring until
  replaced, so continuous presence **overstates reward**.
- **A fill creates inventory**, which the reward estimate ignores
  entirely, so it **understates cost**.

Reward and trading loss are therefore **coupled through fills**, and
both sit on the same unvalidated execution model. The run reports the
hypothetical reward under continuous presence **and** under each
execution scenario's realised presence, so the coupling is visible
rather than assumed away.

### Reported separately, never netted silently

- hypothetical gross reward, per market-date, per allocation and floor
  assumption;
- `L_sim(s)` per execution scenario `s ∈ {queue_ahead 0.00, 0.25, 0.50,
  1.00}`;
- **residual inventory** — carried contracts and their mark — as its own
  line, never folded into `L_sim`.

**Mandatory statement on the screen result:** whenever the conclusion
depends on `L_sim` — which is every PASS and every FAIL on S3/S4 — the
output must read: *"this conclusion depends on an unvalidated execution
model; no fill of ours has been observed."*

---

## 3. L_est, K and the acceptance inequality

Renamed `L_est` → **`L_sim`**, because it is simulated.

| symbol | definition |
|---|---|
| **`R_cons`** | the **25th percentile** across qualifying market-dates of hypothetical gross reward, under a stated allocation assumption and floor aggregation |
| **`R_cons_min`** | the minimum of `R_cons` over the 2 × 3 assumption grid (§4) |
| **`L_sim(s)`** | simulated net trading loss per market-date from the C0 replay on the **same** collected ladders, same markets, same 100-contract size, under execution scenario `s`; residual inventory reported separately |
| **`L_worst`** | `max over s of L_sim(s)` — the least favourable of the four declared queue scenarios |
| **`K`** | capital charge per market-date = `C_mean × 24 h × r_h`, where `C_mean` is mean committed capital over the date and `r_h = r_annual / 8760`. **`r_annual` is a management input**, not derived here. |

**Acceptance inequality — base case:**

```
S3:   R_cons_min  ≥  L_worst + K
```

**Stress scenario — a management assumption, not a statistical result:**

```
S4:   R_cons_min  ≥  2 × (L_worst + K)
```

**The 2× has no statistical justification and I previously implied one.**
I cited the 2.27–2.53 design effect from the fill-transfer work. That
number measures **variance inflation from clustering in a different
dataset** (mirror-lane orders, 2026-09-06..10). It says nothing about
how far a simulated trading loss might be understated here. The
inference does not carry across, and I withdraw it.

**S4 is retained as a declared management stress assumption:** *"require
rewards to cover twice the simulated cost, because the cost is simulated."*
It is a judgement about model risk. **Report S3 and S4 separately** so a
reviewer can adopt either.

---

## 4. "Conservative" is a scenario, not a bound

Taking the least favourable of several **assumed** rules is the worst
case **within the assumed set**. It is not a proven lower bound: the
venue could apply a rule outside the set entirely.

**All six combinations are computed and preserved**, never collapsed:

| unknown | options | measured leverage |
|---|---|---|
| **U1** pool split between sides | `ALLOC_POOLED`, `ALLOC_PER_SIDE` | **11%** ($6.07 vs $6.74 on identical data) |
| **U3** straddling level whole or pro rata | `WALK_WHOLE_LEVEL`, `WALK_PRO_RATA` | **0%** in the tested books; can matter when our level straddles the boundary |
| **Floor aggregation** | A1 per `(market, programId, period)`; A2 per `(market, date)`; A3 per account-date batch | **0% ↔ 100%** |

### The unresolved rule that can flip the decision is the floor aggregation

A market-date estimated at **$0.80** pays:

| | |
|---|---:|
| A1 per (market, programId, period) | **$0.00** |
| A2 per (market, date) | **$0.00** |
| A3 per account-date batch | **$0.80** |

**The same estimate is worth nothing or its full value depending on an
aggregation the documentation never states.** That swing dwarfs U1's 11%
and U3's zero. If the screen's verdict changes between A1 and A3, the
verdict is **not reportable** — it must be stated as *"undetermined,
pending the aggregation rule"*, and resolving it needs
`/v1/incentives/earnings`, which is **authenticated** and outside this
package.

---

## 5. Coverage — how 24 hours can and cannot deliver it

### The unit, and why fragments do not count

For `daily_event` the payout period runs **midnight to midnight Eastern
Time**. A **market-date unit** is one market observed for a **complete
ET date**.

**A run crossing a date boundary yields fragments, not days.** A market
observed 18:00–23:59 ET on date D and 00:00–06:00 ET on D+1 produces
**zero** complete units — not two. The earlier spec would have counted
those as coverage. It was wrong.

### Therefore the run is date-aligned

| | |
|---|---|
| **start** | **00:00:00 ET** |
| **end** | **23:59:59 ET the same date** |
| units obtainable | **exactly one ET date**, across as many markets as are subscribed |
| **so ≥10 market-dates requires ≥10 distinct markets** observed complete on that one date |

The earlier "≥10 market-days across ≥6 markets" was incoherent for a
single-date run. Replaced: **≥10 distinct markets, each complete for the
one ET date.** Feasibility: the retrieved culture programme carried many
Billboard markets sharing one `programId`, so ≥10 concurrent eligible
markets is plausible — **and is itself confirmed at arm**, before the
run starts, by counting `INSTRUMENT_STATE_OPEN` markets with a
`daily_event` period covering the target date.

**Event-bounded periods (`day_of`, `live`) are out of scope** for a
24-hour date-aligned run — they start and end on the event, not the
date. Only `daily_event` is scored.

### Gaps

| condition | treatment |
|---|---|
| any gap > 120 s in a market's date | **that market-date unit is void** |
| cumulative dropped snapshots > 5% of the date | **void** |
| gaps ≤ 120 s | affected snapshots **dropped from the denominator and flagged**; never interpolated |
| every reconnect | gap record with start, end, affected slugs |

### If coverage falls short

The outcome is **INSUFFICIENT COVERAGE** — a third result, distinct from
PASS and FAIL. Report the units obtained and why the rest voided. **The
run is not automatically extended**; extending is a new decision on a
new authorization.

---

## 6. M5 — the blanket claim corrected, still mechanics-only

**Wrong:** "four contracts cannot create eligibility."

**Right:** it depends on the observed book. With Target Size 500, four
contracts create eligibility **iff the side already holds 496–499**:

| side holds | with our 4 | qualifies? | our share |
|---:|---:|---|---:|
| 450 | 454 | no | — |
| 495 | 499 | no | — |
| **496** | **500** | **yes — we created it** | 0.0080 |
| 499 | 503 | yes — we created it | 0.0080 |
| 500 | 504 | yes (already qualified) | 0.0079 |

So four contracts **can** tip a side across the threshold; the window is
narrow and is a property of the book, not of the order size.

**M5 remains mechanics-only.** Even where it qualifies, a 0.8% share of
a $50 pool is $0.40 — under the $1.00 floor. It can establish order
acceptance, cancellation, one fill's accounting if a fill occurs, and
whether a resting order appears in scoring at all. It cannot establish a
fill distribution (n = 1), time-to-fill, profitability, or the economics
of a 100-contract policy, and **it does not close the execution gap**.

**Reward-reporting delay:** calculated within 5 business days of period
end, credited within 2 more — **up to ~7 business days**. A short M5 run
cannot observe its own reward; confirmation needs an **authenticated**
`/v1/incentives/earnings` read that must be budgeted with it.

**M5 is unexecuted and is not requested here.**

---

## 7. Activation specification

| | |
|---|---|
| **code SHA** | pinned in `PACKAGE_B_SHA.txt` beside this file |
| **runner** | `sportsassets-workers` (`srv-d9gcv6urnols73ce6erg`), the existing observation worker |
| **deployment consequence** | merging to `claude/session-njaewf` redeploys **`sportsassets-api` and `sportsassets-workers`** (both track it, `autoDeploy=yes`). Package B adds **no** production code — only `research/beta48/` modules and this document. If Package A is not being deployed, this merge changes no service behaviour. |
| **market selection** | resolved **once at arm** from `GET /v1/incentives?statuses=active&program_type=liquidityProgram&instrument_states=INSTRUMENT_STATE_OPEN`, filtered to markets with a `daily_event` period covering the target ET date. **Frozen for the run.** No mid-run re-selection. |
| **programme versions** | each market stored with `programId`, `createdAt`, `rewardPool`, `discountFactor`, `targetSize`, `period`, `start`, `end`. Parameters **may change between periods**; a changed `programId`/`createdAt` mid-run marks that market-date **partial → void**. |
| **hypothetical quote** | 100 contracts per side at the touch, continuously present, price following the observed best each snapshot. **Hypothetical only — nothing is sent.** |
| **duration** | **00:00:00 → 23:59:59 ET, one date.** Not 72 h, not a rolling 24 h. |
| **subscription cap** | **1** subscription, **≤100 slugs** (SDK-documented ceiling). Reconnects do not create new subscriptions. |
| **HTTP cap** | **≤8 public unauthenticated requests total, including retries and pagination**: ≤4 at arm for universe resolution, ≤2 mid-run parameter re-check, **2 held for retries**. Refuses past the cap. |
| **authenticated calls** | **zero**. Does not touch the 9 account-read requests. |
| **expired probe budget** | **untouched.** This run's HTTP budget is its own and is not drawn from it. |
| **order capability** | `BETTOR_LIVE_MAX_CONTRACTS = 0` |
| **cadence** | stream primary (`bids`, `offers`, `state`, venue `transactTime`); scoring re-sampled to **1 Hz** to match the documented snapshot rate. **30-second snapshots are inadequate for scoring and are not used for it.** |
| **storage** | stream frames to the existing capture path; per-snapshot scoring and per-market-date aggregates to `research/beta48/acceptance/incentive_run_<date>/` |
| **stop** | `UPDATE ingestion_state SET value='false' WHERE key='bettor_live_observation'` — authoritative, effective within `CONTROL_EVERY_S` **in the running process**. `BETTOR_LIVE_LOOP=off` is a cheap pre-check only: a restart does not reload env on this service, only a deploy does. |
| **end conditions** | whichever first: the ET date ends; stream unrecoverable; HTTP cap reached; control set false. **Analysis runs once, after the run stops.** |
| **rollback** | revert + redeploy. The run is read-only; nothing to unwind. |

### Final output

One file, `incentive_screen_<date>.json` + a summary, containing:

1. units obtained, void units and the reason for each;
2. hypothetical gross reward per market-date under **all six**
   assumption combinations;
3. `R_cons` per combination and `R_cons_min`;
4. `L_sim(s)` for all four execution scenarios, **labelled simulated**,
   with residual inventory as a separate line;
5. `C_mean`, and `K` for the management-supplied `r_annual`;
6. **S3 and S4 evaluated separately**;
7. whether the verdict changes between floor aggregations A1 and A3;
8. the mandatory statement that the conclusion depends on an unvalidated
   execution model.

---

## The decision at the end

Exactly one of:

**INSUFFICIENT COVERAGE** — fewer than 10 complete market-dates. Report
what was obtained and why. No extension, no further work on this lead
without a new decision.

**UNDETERMINED** — coverage met, but the verdict flips between floor
aggregations A1 and A3. Report both. The blocker is a documentation gap,
not a data gap; resolving it needs an authenticated earnings read.

**FAIL** — coverage met, verdict stable, and `R_cons_min < L_worst + K`
(S3). Report the numbers. No execution experiment, no M5.

**PASS** — coverage met, verdict stable, S3 holds. Report S4 separately.
This **supports proposing an execution experiment** as a separate
package for separate authorization. **It does not declare profitability**
and it authorizes nothing: every figure on the reward side is
hypothetical, and every figure on the cost side is simulated under an
execution model no fill of ours has ever validated.

---

### Standing correction

The incentive programme is **the most actionable remaining lead in the
work evaluated so far** — not "the only route." Four declared candidates
are negative under the tested execution assumptions on one 12-market,
8-day corpus that is now fully consumed. The space of policies was never
enumerated.
