# Engine delivery — schedule v2, against concrete deliverables

**2026-09-23.** Replaces the 50-hour plan in
`ENGINE_DELIVERY_CHECKPOINT_2026_09_23.md`. Three things in that
document were wrong and are corrected here before anything is scheduled.

---

## 0. Three corrections to the previous checkpoint

**0.1 — I generalized one dataset into a statement about the system.**
The 4,879 rows were `bettor_state_observations`: the read-only
prospective PMUS unselected state capture, a purpose-built sampling
experiment. I read its properties as the system's data situation. Its
actual identity:

| | |
|---|---|
| Collector | `bettor_state_capture` → `bettor_state_store` |
| Venue / endpoint | PMUS, `GET /v1/markets/{slug}/book` |
| Table | `bettor_state_observations` (+ `bettor_state_mids` for follow-ups) |
| Range measured | 2026-09-20 19:23Z → 2026-09-23 17:06Z (~69.7 h) |
| Sampling rule | deterministic rotation, `slice = sha256(UNIVERSE_VERSION\|id) % 787`, one bucket per 300 s, ≤40 markets/cycle spread over 5 ticks |
| Full rotation | 787 × 300 s = **236,100 s = 65.58 h**, prime and not commensurate with a day so sample times precess |
| Dedup | one observation per market per 300 s bucket, enforced by primary key |

**So "1.003 rows per market" was the rotation working exactly as
designed** — my window was barely one full pass. It was not a collector
that "looks once, ever." And repeat observations of the same market do
exist; they land in `bettor_state_mids` at declared horizons
(60/300/900/3600 s), a table I did not look at.

**0.2 — The collector's request shape is not the binding constraint,
because the primary objective does not run on that dataset.** Beside it:

| Source | Rows | Span |
|---|---|---|
| `trades` — cohort fills, Polymarket global | **6,122,489** | 2024-11-16 → 2026-09-23 |
| `markets` resolved with observed payout | **123,364** of 141,002 | — |
| `bettor_state_observations` — PMUS capture | 4,893 | 3 days |
| `bettor_experimental_observations` — X lane, PMUS | 28,602 over 384 symbols | 3 days |
| **`live_orders` — OUR real orders** | **166,585** | — |
| `engine_fills` — OUR shadow recommendations | 349,411 | — |
| `mirror_books` | 1,631 over 1,191 conditions | — |
| `price_path` | 8,953 | — |
| `bettor_capture_ticks` | 3,543 | — |
| `shadow_executions` | **0** | — |

Three orders of magnitude, and nearly two years. My "binding constraint"
was measured on the smallest dataset in the system.

**0.2a — And two of the sources I wrote off are real.**

`bettor_experimental_observations` holds **28,602 rows over 384 symbols**,
with up to **3,578 samples on a single symbol**. That is a genuine
repeat-sampled time series on PMUS — migration 080 exists precisely
because the decision-grade collector could not produce one. My claim
that "no contract has a time series" was false on this source.

`live_orders` holds **166,585 REAL Polymarket CLOB orders placed by the
copier**, each with the raw API response, `status` in
(submitting, filled, unfilled, rejected, error, settled),
`filled_shares`, `fill_price` and `reaction_s`. This is real execution
evidence and I had dismissed it.

**What it is and is not.** `limit_price` is documented as a *FOK limit
(his price + max slippage cap)* — fill-or-kill, so these are **TAKER**
orders. They never rested in a queue, so they say nothing about whether
a resting order would fill: **maker P_FILL remains NOT_IDENTIFIED.** But
they are a large, real record of *taker* execution — did an aggressive
order at a given limit fill, how much of it, at what price, after what
latency — and **EXIT_NOW and COMPLETE_PAIR are taker actions.** So the
execution model for the actions our management policy can actually
select is estimable from data we already hold.

`engine_fills` (349,411) is the opposite case and must stay out of it:
migration 004 calls it "internal engine recommendations (shadow fills) …
what OUR model *would* trade". Training an execution model on those
would be training on assumed simulator fills as though they were
executions, which is forbidden by name.

**0.3 — I withdraw the collection request.** I was about to ask to
double per-market reads to fetch a sibling leg. Verified instead: the
stored payload has six keys — `bids, offers, stats, marketSlug,
transactTime, state` — one instrument's ladder, no second side. Both of
my explanations for the empty `no_ask` were wrong, and the request built
on them is withdrawn. **No new collection is required for any deliverable
below.**

---

## 1. What changed in the code today

`bettor_desk.Policy` had a management vocabulary — ENTER, COMPLETE_PAIR,
REDUCE, EXIT, HOLD, AMEND, CANCEL — decided by rules with **no EV
attached to any of them**. It could not say what completing a pair was
worth, so it could not be compared against holding, and nothing could be
improved against it. Two modules now exist:

**`bettor_mgmt_value`** — the management actions priced in the same
`Candidate` contract the acquisition engine uses. HOLD is priced and its
value is not zero. The selection rule refuses the failure that would
otherwise follow: live, HOLD has no value and EXIT does, so ranking by
EV alone selects EXIT every time — an engine that liquidates everything
for want of a settlement model. If HOLD is unpriced, nothing is
selected.

**`bettor_mgmt_test`** — one position handed identically to every
declared policy and managed through the subsequent evidence. On a worked
seed:

```
settles 1.00   HOLD +55.00   COMPLETE +6.52   COHORT +73.69
settles 0.00   HOLD -45.00   COMPLETE +6.52   COHORT -76.31
```

Completion returns **the same +6.52 whichever way the event resolves** —
the pair pays 1.00 per contract regardless. That is the Ferrari
mechanism working, and it **loses to holding when our leg wins**. The
honest summary is variance reduction at a cost in mean, and a test
asserts `COMPLETE < HOLD` on the winning seed so no later summary can
call it profitable.

At `queue_share = 0.10` only 40 of 100 contracts pair; 60 stay
directional and the result swings (+35.61 / −24.39). **A single assumed
queue share would have reported a clean lock that does not exist.**

I shipped look-ahead in the first version and found it: `_mk_market`
handed the decision the settled payout. It is withheld now and used only
to score. 29 tests, six controls.

---

## 2. The six deliverables

Hours are engineering only. Where a thing is not engineering-bound I say
so rather than converting it into hours.

| # | Deliverable | Hours | Depends on | Necessary because |
|---|---|---|---|---|
| **D1** | **Recovery acceptance** | 8 | — | The desk is paused and cannot produce prospective records until it is verified. Everything prospective sits behind this. |
| **D2** | **Historical cohort-seeded management demonstration** | 14 (10 done) | — | Scores our management policy against hold-to-settlement and the cohort, on 123,364 settled markets. Needs no entry model, no p_fill, no new collection. |
| **D3** | **Live seeded shadow integration** | 10 | D1, D2 | The same implementation, seeded from live cohort fills, producing prospective records. This is what a qualification window would run on. |
| **D4a** | **Taker execution model** | 16 | — | **Revised up from "8 to prepare".** 166,585 real FOK orders support it now: fill / partial / unfilled at a stated limit, realized price vs. limit, and reaction latency. Validated out of sample, with the supported operating range exposed and extrapolation refused. This covers the actions the management policy actually selects. |
| **D4b** | **Maker P_FILL** | 0 useful now | a pilot | A FOK order never rests, so these 166,585 orders cannot speak to queue position. Needs our own resting orders — one bounded pilot, prepared against D4a's schema. |
| **D5** | **Independent entry valuation** | 0 useful now | research | Not engineering-bound. `P_BETTOR_INDEPENDENT_V3` measured the blend **worse** than the venue price by 0.00926 log loss, CI [−0.00222, +0.02036], NOT_DETECTED. Hours do not move this. |
| **D6** | **Prospective qualification** | 6 + a window | D3 | A pre-registered protocol over D3's records. The window is calendar time, not hours. |

**Earliest usable delivery: D2.** It runs today on data we hold.

### The critical path, and it does reach qualification

    D1 ──┐
         ├──► D3 ──► D6   qualified MANAGEMENT policy
    D2 ──┘

    D4 ──► maker actions        (widens D3; needs our own orders)
    D5 ──► independent entry    (widens D3; research-bound)

My previous note said "A is not on the critical path to B or C." That
framing was wrong, and it described a schedule that made no progress
toward qualification. The correction is specific:

**Qualifying a MANAGEMENT policy requires neither D4 nor D5.**
Completion and exit executed as **taker** actions against displayed
depth need no fill probability — that is why `requires_our_fill` is
False on both and True on REPRICE. And a completed pair contains no
settlement forecast, so it needs no entry model. D4 and D5 widen the
action set; they do not gate qualification of what D2 and D3 test.

D1 → D3 → D6 is therefore a complete path from here to a prospectively
qualified policy, and it is the path I am on.

---

## 3. Dates, separated

- **D2 — historical management demonstration: 2026-09-24.** Assumes the
  seed loader needs one index (`trades(condition_id)` built
  CONCURRENTLY — flagged, not applied: a 6.1M-row table).
- **D1 + D3 — live seeded shadow: 2026-09-26.** Assumes recovery
  acceptance passes on the first corrected-build restart.
- **D6 — prospective qualification: no date.** It is a window, and
  naming a completion date for it would be naming a date for evidence
  that has not been collected. The protocol is datable (with D3); the
  qualification is not.

I am not putting one date on all three, and D6's absence is the point
rather than an omission.

---

## 4. Collection requests

**None.** D1, D2 and D3 run on `trades`, `markets` and the existing desk
tables — all already collected, nothing added, nothing re-read.

The only genuinely unavailable measurement is **our own fill rate**, and
no collector change can produce it: it needs our own resting orders. The
observational substitutes are each forbidden by name and each for a
sound reason — whale completion (their size, their order policy, which
is NOT_IDENTIFIED), a touch (nobody traded with us), a price move
(volume existed somewhere), displayed depth (what could have traded).
That is a bounded pilot request, and I will prepare it against D4's real
feature schema rather than an improvised one, so it can say exactly what
it would resolve. It is not requested today.

---

## 5. Current blocker

D2's seed loader against a 6.1M-row table. `count(*)` took 4.5 minutes
and a `DISTINCT condition_id` blew a 240-second timeout, so the loader
needs a bounded seed window and probably one index. That is ordinary
engineering and it is what I am doing next.

---

## 6. RN1-seeded position-management experiment — status per step

Asked for directly. **It is not running.** Step by step, with no step
reported better than it is.

| # | Step | Status | Detail |
|---|---|---|---|
| 1 | **Source event** — detect an RN1 entry | **IMPLEMENTED, RUNNING** | RN1 entries land as `trades` rows (on-chain Polygon listener) and the `rn1_obs_*` tables carry the observation lane. 6,122,489 fills to 2026-09-23 17:19Z. |
| 2 | **Classify** initial entry / addition / pair completion | **MISSING** | No `entry_kind` anywhere in the codebase. Nothing distinguishes an opening buy from an add or a completion, so every RN1 purchase would currently read as a new entry — exactly the conflation you named. Task #56 has been open on this. |
| 3 | **Create a separately labelled shadow position with assigned inventory** | **MISSING** | The desk has no seeding path. `bettor_desk.Policy` decides its own entries (`bettor_desk.py:894–913` is an ENTER/NO_TRADE branch); there is no way to hand it a position. `bettor_mgmt_test.Seed` is my new harness's own structure and is **not wired to the desk**. |
| 4 | **Evaluate the alternatives** | **IMPLEMENTED, NOT RUNNING** | `bettor_mgmt_value.compare` — HOLD / EXIT_NOW / REDUCE / COMPLETE_PAIR / WAIT / REPRICE, each priced or refused by name. Built today. 17 tests. Not yet reached by any live path. |
| 5 | **Select an action** | **IMPLEMENTED, NOT RUNNING** | Two declared policies. Live, `VALUE_RANKED_V1` selects nothing, because with no settlement view HOLD has no price and nothing can be ranked against it. `COMPLETE_ON_LOCKED_GAIN_V1` can act on a declared risk preference. |
| 6 | **Order state** | **IMPLEMENTED, PAUSED** | `bettor_desk.Order` carries the full state machine (PROPOSED / RESTING / PARTIALLY_FILLED / FILLED / CANCEL_PENDING / CANCELLED / EXPIRED / REJECTED). The desk that drives it is paused for accounting recovery. |
| 7 | **Resulting inventory** | **IMPLEMENTED, PAUSED** | `bettor_desk.Portfolio` with per-leg qty, basis, realized and fees. Persisted per account. |
| 8 | **Reconciled P&L** | **IMPLEMENTED, PAUSED, AND UNCERTAIN** | The ledger identity `cash + inventory_cost − realized == starting_cash` holds at every read. It is a consistency check, not a completeness one. Account `acct_fc2d773a2afa4851` remains `ACCOUNTING_UNCERTAIN` with performance qualification SUPPRESSED. |
| 9 | **Track RN1's subsequent actions as the benchmark** | **IMPLEMENTED, NOT RUNNING** | `policy_cohort_mirror` in the harness. Observed, at their size and their order policy — `WHALE_ORDER_POLICY` is NOT_IDENTIFIED, so it is a benchmark and not an achievable return. |
| 10 | **Venue-supported capital release** | **REFUSED, not missing** | `bettor_merge` returns `permitted=False` for both retail and institutional. "No merge mechanism has been observed" is not "the venue lacks one", so it is UNSUPPORTED rather than absent. Until observed, a completed pair releases capital only at settlement. |

**Assigned-entry testing vs. executable replication, kept apart.**
Everything above is *assigned-entry*: the position is handed over at
RN1's own fill price, which we could not have had. Executable
replication — could we have obtained a position after detecting the
event, at prices then available — is a different and harder question,
and `reaction_s` in `live_orders` is the evidence for it. Not started.

**Which decisions use what.**

| Decision | Basis |
|---|---|
| Settlement value of HOLD | **No trained model.** Refused live (`SETTLEMENT_NOT_ESTIMATED`); exact on settled history, used only to score. |
| Completion value | **Arithmetic, no model.** `1.00 − own cost − complement cost − fees`. |
| Exit value | **Arithmetic** over a displayed bid — and in the historical test the "bid" is an **assumption**: `trades` holds executions, not quotes. |
| Our fills | **Assumption.** `PRINT_THROUGH_WITH_QUEUE_SHARE_V1`, swept over 0.10 / 0.25 / 0.50 rather than fixed at one value. |
| Entry admission | **Trained model, and it refuses.** `P_BETTOR_INDEPENDENT_V3`: blend worse than the venue price by 0.00926 log loss. |
| Selection rule | **Rule**, and a declared risk preference for the completion policy. |

**Command-centre click path:** there is none for this experiment yet —
nothing writes RN1-seeded positions, so there is nothing to display, and
I am not going to point you at a page that would render an empty panel
as a feature. The live shadow account's own path is: *Shadow desk — LIVE
+ REPLAY* → the LIVE block at the top, currently showing
**PAUSED — ACCOUNTING RECOVERY**.

**One traceable example:** not available from the running system. The
worked example in §1 is from the harness on a constructed seed, and it
is labelled as such.

### What steps 2 and 3 need — the actual next work

1. `entry_kind` classification over `trades`: initial entry, addition,
   pair completion, reduction — from the account's running position on
   that condition, which is reconstructible from its own fill history.
2. A seeding path on the desk: assign inventory without an ENTER
   decision, under a separate experiment label, so the seeded book never
   mixes with the desk's own.

Neither needs new collection. Both are ahead of any further pairing
work, which is not a substitute for this experiment and is not being
treated as one.
