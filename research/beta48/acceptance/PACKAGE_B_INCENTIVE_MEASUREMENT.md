# Package B — bounded incentive-share measurement

**Not activated.** No orders, no deployment, no probe re-arm. Separate
from the expired observation budget and from the nine remaining
account-read requests. $0.00 at risk.

Code: `research/beta48/bettor_incentive_score.py`,
tests `research/beta48/test_bettor_incentive_score.py` — **10 passing**,
including the venue's own worked example reproducing 70.6% / 1.9%.

---

## 1. The reward calculation, implemented

`100/(100+C)` is gone. It was a special case requiring: our order at the
best price, every competitor also at the best price, the side already
meeting Target Size without us, both sides qualifying equally often, and
an even pool split. It is wrong the moment any of those fails.

### Rules implemented (documented, retrieved 2026-09-22T18:14Z)

| | rule |
|---|---|
| R1 | `Score = discountFactor ^ (ticks from best) × OrderSize` |
| R2 | a random book snapshot every second |
| R3 | Target Size is a **raw aggregate**; the exchange walks from the best price outward until it is met; orders beyond that range do not score |
| R4 | each snapshot: bid and ask **each** normalised to 1.0, **provided Target Size is met on that side** |
| R5 | sides scored independently; our own spread is irrelevant |
| R6 | no per-person cap |

**C is competing *qualifying, discounted* score, not raw size.** The
walk uses raw size to decide *which levels* score; the discount is
applied only after. Reversing those gives a different answer whenever
the discount would have changed the walk.

**Qualifying uptime is a first-class output.** A side that fails Target
Size contributes nothing and is not normalised. `period_reward()`
integrates over a period's snapshots and divides by the number of
**qualifying side-snapshots**. A single snapshot's share is never
multiplied by a pool.

### Our order changes the book — both directions modelled

The book is scored twice, without us and with us inserted; shares are
never adjusted after the fact.

| | effect |
|---|---|
| **E1** | we can **create eligibility**. A side holding 450 against Target Size 500 does not qualify — until our 100 arrives. Then it qualifies and we hold 100/550. |
| **E2** | we can **push others out**. Our size consumes Target Size earlier, so worse-priced orders fall outside the walk, raising our share above any naive `1/(1+C)`. |

### Worked contrasts, from the implementation

| BID book, our 100 at 0.50, pool $50, DF 0.25, TargetSize 500 | `100/(100+raw)` | **actual** |
|---|---:|---:|
| competitor 400 at our price, plus 200 behind | 0.143 | **0.200** |
| competitor 400 one tick behind (discounted to 25%) | 0.143 | **0.500** |
| competitor 600 one tick **ahead** — we fall outside the walk | 0.143 | **0.000** |
| thin side, 450 only — qualifies **only because we arrive** | 0.182 | **0.182** ✓E1 |

### Declared unknowns — assumptions, not facts

| | unknown | handling |
|---|---|---|
| **U1** | **how the pool splits between the two sides.** R4 normalises each side to 1.0 per snapshot but does not say whether the pool is 50/50 per side or shared across all qualifying side-snapshots. | both implemented (`ALLOC_PER_SIDE`, `ALLOC_POOLED`); **both always reported**. In a test case they give $6.07 and $6.74 from identical data. |
| **U2** | **how the pool splits between overlapping periods** on one market-day | periods computed separately; **never summed without saying so** |
| **U3** | whether a level straddling the Target Size boundary is included **whole or pro rata** | both implemented (`WALK_WHOLE_LEVEL`, `WALK_PRO_RATA`) |

**A regression I introduced and caught.** My first implementation walked
*orders* rather than *price levels*, so a competitor listed first
exhausted Target Size and excluded us **at our own price** — reproducing
the exact same-level-exclusion error this module exists to correct. Two
tests now pin it, including one asserting that the order of entries
within a level cannot change the answer.

---

## 2. The payout floor — documented rule vs unresolved aggregation

**Documented, verbatim:** *"Rewards under $1.00 are not paid out."*

**That is the whole of it.** The documentation does **not** say at what
level the $1.00 is assessed.

**What I wrongly inferred.** I claimed the unit is `(market, date)`
because `GetIncentivesEarnedResponse` groups that way and the endpoint
says each entry *"sums all payouts for a single (market, date) pair."*
That describes **how a reporting endpoint groups its rows**. It does not
establish that the floor is applied to that grouping, and it certainly
does not establish that eFootball's `$35 day_of` and `$100 live` become
**one economically interchangeable pool**. They are distinct
`programId`s with distinct pools; a reporting sum is not a pooling rule.

**Three candidate aggregations, all carried:**

| | floor applied to | effect |
|---|---|---|
| **A1** | each `(market, programId, period)` earning separately | **strictest** — small per-period amounts forfeit individually |
| **A2** | the `(market, date)` total | the reporting grouping; middle |
| **A3** | the whole account-date payout batch | **most permissive** |

**Every reported figure carries all three.** The decision rule in §4
uses **A1, the strictest**, because a screen that passes only under the
most generous unproven assumption is not a screen.

**Resolving it needs `/v1/incentives/earnings`** — authenticated, and
therefore an account request, not a public one. It is *not* part of this
package.

---

## 3. No cross-market subtraction — rewards are a budget, not an edge

**Withdrawn:** "one culture market-day at C = 400 covers 6.8 days of the
$1.47/day shortfall."

The $1.47/day came from **C4 on nine sports markets** — NFL, CFB, Liga
MX, boxing — with 100-contract clips over 2026-09-13..20, with 30 of 44
episodes concentrated in two Liga MX markets. **Culture markets are a
different population under a different policy.** Subtracting one from
the other compares nothing to nothing.

**What can be said, and all that can be said**, is a ceiling:

| competitor at our level | reward $/market-day (100 contracts) | **max daily trading loss it could absorb** |
|---:|---:|---|
| 200 | — | **nothing** — side never reaches Target Size |
| 400 | 10.00 | $10.00 |
| 900 | 5.00 | $5.00 |
| 1,900 | 2.50 | $2.50 |
| 4,900 | 1.00 | $1.00 |

These assume **100% qualifying uptime on one side**. Measured uptime
scales every row **down**.

**Trading loss, inventory exposure and capital requirement for the
proposed culture markets at the proposed size are UNMEASURED.** The
collection in §6 therefore does both: it measures the reward share *and*
replays the quoting policy on the same collected ladders, so the credit
and the cost come from **one market population, one size, one policy**.

---

## 4. The decision rule, derived rather than picked

The earlier "median $1, upper-quartile $3" was arbitrary. Replaced.

**Screen (all four must hold):**

| | requirement | why this number |
|---|---|---|
| **S1 coverage** | ≥ 10 distinct eligible market-days, ≥ 6 distinct markets, ≥ 80% stream uptime per market-day | below ~10 market-days the between-market spread in competition is unestimable; 6 markets so one market cannot carry the result |
| **S2 conservative reward** | `R_cons` = 25th percentile of measured per-market-day reward, computed with: `ALLOC_PER_SIDE` (the less favourable of U1 wherever they differ), worse-price exclusion counted, **floor applied under A1**, and measured uptime | a screen must pass on the unfavourable branch of every unresolved assumption, or it is not a screen |
| **S3 cost coverage** | `R_cons ≥ L_est + K` where `L_est` is the **measured** replayed trading loss per market-day on the same markets and size, and `K` is the capital charge on the same committed capital | this is the actual economic condition; anything else compares populations that were never matched |
| **S4 margin** | `R_cons ≥ 2 × (L_est + K)` | the replay is a model with known unresolved execution uncertainties; a 2× margin is the smallest that survives the design effect of ~2.3–2.5 measured in the fill work |

**Passing S1–S4 justifies *proposing* an execution experiment. It does
not authorize one.** Authorization is a separate decision on a separate
package. Failing any of S1–S4 ends the line of work: the collection is
the off-ramp, not a formality before a predetermined escalation.

---

## 5. M5, aligned — it tests mechanics, and will likely be paid nothing

**A four-contract quote cannot test the economics of a 100-contract
policy.** From the implementation, at pool $50 / DF 0.25 / Target Size
500:

| competitor at our level | **4 contracts** | 100 contracts | 4c payable? |
|---:|---:|---:|---|
| 0 | $0.00 | $0.00 | no — side below Target Size |
| 396 | $0.00 | $0.00 | no — 400 total still below 500 |
| 900 | $0.22 | $5.00 | **no — under the $1.00 floor** |
| 1,900 | $0.11 | $2.50 | **no — under the floor** |

At four contracts the side usually fails Target Size, and where it
qualifies the payout is under the floor. **E1 will not trigger** — four
contracts cannot create eligibility.

**What M5 can establish:** order acceptance and acknowledgement,
cancellation and its acknowledgement, one fill's accounting if a fill
occurs, and whether a resting order appears in scoring at all.

**What it cannot:** a fill distribution (n = 1), time-to-fill,
profitability, or the economics of any larger size. **It does not close
the execution gap.**

**Reward-reporting delay:** calculated within 5 business days of period
end, credited within 2 more — **up to ~7 business days**. A 30-minute M5
run cannot observe its own reward. Confirmation needs a later read of
`/v1/incentives/earnings`, which is **authenticated** and would spend an
account request. That read must be budgeted *with* M5, not assumed.

**M5 remains unexecuted and is not requested.**

---

## 6. The 24-hour collection, specified exactly

| | |
|---|---|
| **universe, pinned** | markets from `GET /v1/incentives?statuses=active&program_type=liquidityProgram&instrument_states=INSTRUMENT_STATE_OPEN`, resolved **once at arm** and frozen for the run. No mid-run re-selection. |
| **programme version pinned** | each market stored with its `programId`, `createdAt`, `rewardPool`, `discountFactor`, `targetSize`, `period`, `start`, `end`. The docs state parameters **may change between periods**; a changed `programId`/`createdAt` mid-run marks that market-day **partial** and it is excluded from S1 coverage. |
| **hypothetical quote rule** | 100 contracts per side, **at the touch**, both sides, continuously present for the whole period. Hypothetical only — nothing is sent. Quote price follows the observed best price each snapshot. |
| **cadence** | the **stream** is primary (`bids`, `offers`, `state`, venue `transactTime`). Reward scoring re-sampled to **1 Hz** to match R2. **30-second snapshots are explicitly inadequate** and are not used for scoring. |
| **also computed** | the C0 quoting replay on the **same** ladders, for `L_est` in S3 |
| **coverage requirement** | per S1. Gaps > 120 s void that market-day for scoring; every reconnect writes a gap record with start, end and affected slugs; gaps are **labelled holes, never interpolated** |
| **subscription limit** | 1 subscription, ≤ 100 slugs (SDK-documented ceiling) |
| **HTTP limit** | ≤ **6 public** `/v1/incentives` reads for universe + one mid-run parameter re-check. **Public and unauthenticated.** |
| **budget separation** | **does not touch** the expired observation probe budget, and **does not touch** the 9 remaining account-read requests. No authenticated venue call at all. |
| **storage** | stream frames to the existing capture path; per-snapshot scoring output and per-market-day aggregates to `research/beta48/acceptance/` |
| **order capability** | `BETTOR_LIVE_MAX_CONTRACTS = 0`. No order can be sized. |
| **shutdown** | `UPDATE ingestion_state SET value='false' WHERE key='bettor_live_observation'` — authoritative, effective within `CONTROL_EVERY_S` in the running process. `BETTOR_LIVE_LOOP=off` is a cheap pre-check only; a restart does not reload env on this service, only a deploy does. |
| **end conditions** | whichever first: 24 h elapsed; S1 coverage met; stream unrecoverable; control set false. **Analysis runs once, after the run stops.** |
| **duration** | 24 h, not 72 |

### Acceptance — collection quality only

- [ ] running SHA matches the pin
- [ ] `orders_submitted = 0`; `BETTOR_LIVE_MAX_CONTRACTS = 0`
- [ ] ≥10 eligible market-days, ≥6 markets, ≥80% uptime each
- [ ] every frame carries venue `transactTime` **and** receipt time
- [ ] every reconnect has a gap record
- [ ] programme parameters stored per market with `programId` + `createdAt`
- [ ] reward reported under **both** U1 allocations and **all three** floor aggregations
- [ ] `L_est` computed on the same markets, same size, same policy
- [ ] public read count reported against the ≤6 plan; **0 account requests**

**This is data acceptance. It is not evidence of profit.**

---

## The decision at the end

When the run stops, exactly one of:

**PASS — S1, S2, S3 and S4 all hold.** The incentive route survives its
first honest test on matched data. *Propose* an execution experiment as
a separate package for separate authorization. Nothing is authorized by
this result.

**FAIL — any of S1–S4 fails.** Report which, with the measured numbers.
No further collection on this lead, no execution experiment, no M5.

---

### One correction to my own language

I called the incentive programme **"the only route."** It is not. The
evidence establishes that **four declared candidates** — C0, C2, C3, C4
— are negative under the tested execution assumptions on one 12-market,
8-day corpus. That is not a demonstration that every possible trading
policy fails; the space of policies was never enumerated, and the
corpus is small, concentrated and now fully consumed.

The accurate statement: **the incentive programme is the most actionable
remaining lead in the work evaluated so far.**
