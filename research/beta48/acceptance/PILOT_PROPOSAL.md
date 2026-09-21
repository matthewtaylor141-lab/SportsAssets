# MEASUREMENT PILOT — proposal for explicit approval

**This is a proposal. No order is activated by this document, and nothing in
the repository will submit one.** `mirror_live=false` and the execution gate
stay as they are until a separate authorization changes them.

**Its deliverable is a `p_fill` measurement, not profit.** A pilot that made
money and taught us nothing about fill probability would have failed.

> **v2 — four defects in v1, all mine, corrected below.** The `$5` figure was
> not worst case; the `$25` realized-loss trigger could be passed before it
> fired; §3 and §6 gave different stopping rules; and the claim that *"a
> 1-contract fill teaches as much about queue position as a 1,000-contract
> fill"* is **withdrawn** — §3.4 shows it is false on the venue's own fee
> arithmetic before any queue argument is reached.

---

## 1. WHY THIS CANNOT BE ANSWERED FROM PUBLIC DATA

Public book data shows what the market did. It cannot show what **our** resting
order would have done, because that depends on queue position behind orders we
cannot see, and on whether our presence changed the taker's behaviour.
`bettor_shadow_execution` already refuses to infer it, and
`QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND` is a retracted claim preserved in that
module precisely because an earlier attempt to bound it failed.

**What public data CAN bound, and should before any pilot:** the arrival rate
of trades at each price level, the spread distribution, and how often a level
is fully consumed. Those bound the *opportunity*; they do not identify
`p_fill`. That work is read-only, costs nothing, and is listed in §9.

## 2. ACCOUNT, VENUE, FEES, ELIGIBLE MARKETS

| | |
|---|---|
| Venue | `polymarket-us` |
| Account | **institutional — IDENTITY UNVERIFIED.** `account_snapshot()` returns no wallet, address or account id. **`reconcile_read.capability_probe()` and `account_identity()` must run first**; a pilot on an account we cannot name is not a pilot. |
| Fee schedule | **RESOLVED — `PMUS_PUBLISHED_2026_09_17`**, implemented in `bettor_fee_schedule.py` and wired into the engine. Status `PUBLISHED`, **not** `VERIFIED_APPLIED`: the venue documents these terms and we have never seen them on a statement for this account. Verifying application is a pilot *output* (§5), not a prerequisite. |
| `holds_both_legs_independently` | **UNKNOWN.** Must be SUPPORTED or the pilot is single-leg only. |
| Eligible markets | Only contracts meeting **all**: `MARKET_STATE_OPEN`; two-sided book; spread ≥ 2 ticks; a **five-level ladder whose top level agrees with the quote** (§3.5); source-clock freshness ≤ 10 s at decision with a receipt timestamp present; event start > 60 min away. |

## 3. SIZE AND EXPOSURE

### 3.1 One combined limit, because exposure is one quantity

v1 gave four separate numbers — position contracts, cash per market, total
deployed, open markets — and computed "$5 worst case" from **filled inventory
alone**. A book could therefore sit at its deployment limit in inventory and
carry live quotes, a cancel-pending order and an unrecovered leg *on top of
it*, each admitted against a different limit.

`ShadowLoop.combined_exposure` is now the single number, and
`RiskLimits.max_combined_exposure` is the single limit:

```
combined_exposure = remaining basis of filled inventory
                  + reservations for every LIVE quote, which includes
                    PARTIAL remainders and CANCEL_PENDING orders
                  + recovery commitments on unpaired legs
```

**Recovery commitments are real even though no order exists for them.** An
unpaired leg will either be completed — buying the complement, up to $1.00 per
contract — or exited. Until one happens that cost is committed, and a limit
that cannot see it admits new positions against cash already spoken for.

**A rebate never reduces a reservation.** The published maker fee is negative;
netting it against the reservation would admit a quote against cash that only
exists if the rebate arrives. `quote_reservation` floors the fee at zero.

### 3.2 The pre-trade budget, which a realized-loss trigger is not

A trigger on cumulative *realized* loss is always breached after the fact: by
the time realized loss reaches $25, everything still outstanding can lose more
on top of it. The budget is checked **before each order**:

```
worst_case_loss = realized loss so far
                + for each holding: matched pairs can lose only their cost
                  ABOVE par; the unpaired remainder can lose its whole basis
                + for each live quote: its full reservation, assuming the
                  remainder fills at its own price and settles worthless
```

`RiskLimits.max_worst_case_loss` refuses any order that would push this past
the budget. Both limits are enforced in `bettor_shadow_loop.py` and tested
(`TestCombinedExposureAndTheLossBudget`), not described here and left to
operator discipline.

### 3.3 A replacement never overlaps the order it replaces

**A cancel is a request, not an event.** Between sending it and the venue
acknowledging it the order is still in the market and can still fill. v1's
`reprice_quote` set the old quote to `REPRICED` and rested the replacement in
the same call — so two orders for one intended position could be live at once
and both could fill, **doubling exposure through the act of managing it**.

The protocol is now two-phase:

| step | state | reserved? | fillable? |
|---|---|---|---|
| `cancel_quote` / `reprice_quote` | `CANCEL_PENDING` | **yes** | **yes** |
| `confirm_cancel` | `CANCELLED` | released | no |

The replacement is **deferred** and placed by `confirm_cancel`, after the
original is confirmed gone. It is pre-checked at request time (so an
inadmissible replacement never causes a cancel at all) and re-checked at
placement (since anything may have filled in between). A fill during the
pending window shrinks the replacement; a full fill leaves nothing to replace
and the order is recorded `FILLED`, not `CANCELLED` — the cancel arrived too
late and saying otherwise would misstate what happened to the contracts.

### 3.4 Size — and the withdrawn claim

v1 said: *"a 1-contract fill teaches as much about queue position as a
1,000-contract fill."* **That is false, and the venue's own fee schedule
refutes it before any queue argument is reached.** Round trip at p = 0.485,
maker in / taker out, unchanged book, under `PMUS_PUBLISHED_2026_09_17`:

| contracts | entry rebate | exit charge | **round trip per contract** |
|---|---|---|---|
| **1** | 0.00 | 0.02 | **+0.02000** |
| **2** | −0.01 | 0.03 | **+0.01000** |
| 5 | −0.02 | 0.09 | +0.01400 |
| 10 | −0.03 | 0.17 | +0.01400 |
| 100 | −0.31 | 1.74 | +0.01430 |
| 1000 | −3.12 | 17.36 | +0.01424 |

Fees round to the cent **per fill**, so a 1-contract fill's rebate
(−0.0125 × 0.485 × 0.515 = $0.0031) rounds away entirely while the taker
charge rounds up. The 1-contract round trip costs **40% more per contract**
than the asymptote, and the 2-contract one costs **30% less** — the
per-contract economics at these sizes are not merely different from scale,
they are **non-monotone**. A pilot at 1 contract measures the economics of a
size nobody would trade at.

`min_contracts_for_a_cent` under this schedule:

| price | 0.05 | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 |
|---|---|---|---|---|---|---|
| contracts before the rebate is non-zero | 9 | 5 | 3 | 2 | 2 | 2 |

**Proposed size: 5 contracts, priced ≤ $0.50.** Five is the smallest clip
whose per-contract fee is within 1.5% of the asymptotic rate at every eligible
price, so the fee term measured is the one that would apply at scale.

**What the pilot still cannot establish** is that `p_fill` at 5 contracts
equals `p_fill` at 500: a larger order sits differently in the queue and may
move the market it is measuring. **Scaling from this pilot to $500k/day is not
licensed by it**, and the capacity analysis must be redone with pilot data
rather than extrapolated from it.

### 3.5 Parameters

| parameter | proposed | derivation |
|---|---|---|
| Quote size | **5 contracts** | §3.4 — the smallest clip whose fee per contract is the one that applies at scale |
| Max price | **≤ $0.50** | caps worst-case loss per contract at $0.50 |
| Simultaneous live quotes | **4** | one per market |
| **`max_combined_exposure`** | **$10.00** | 4 × 5 × $0.50, covering inventory, live quotes, cancel-pending orders and recovery commitments together |
| **`max_worst_case_loss`** | **$25.00** | checked pre-trade against realized loss plus worst-case outstanding, so it cannot be passed before it fires |
| Max unhedged directional exposure | **20 contracts** | every fill is unhedged by construction |

Sizing uses the ladder's **top level**, never `yes_depth`'s five-level sum:
that column reports 4903.69 on a row whose quote has 17 contracts behind it.

## 4. CONTROLS

- **Cancellation policy, which is part of the measurement (§5.1):** every
  quote rests for a maximum of **300 s**, then cancel. Cancels are ungated by
  design — the execution gate never blocks them.
- **Partial fills:** the remainder stays reserved and live; recovery policy
  runs on the filled portion.
- **One-leg exposure:** the existing ordered policy — COMPLETE if the
  complement is executable and the incremental cash beats the exit, else EXIT
  at the bid, else UNRESOLVED_EXPOSURE, named and escalated. Requires a fresh
  recovery read; a stale one refuses.
- **Every order passes the execution gate** (`authorize("submit", lane=...)`),
  which fails closed on an unreadable kill switch.

## 5. WHAT IS MEASURED

### 5.1 `p_fill` — the definition, since the number is meaningless without it

> **`p_fill` is the probability that a quote of 5 contracts, resting at the
> touch, is filled **in full** within its 300-second lifetime, before the
> cancellation policy removes it.**

Every clause is load-bearing and v1 stated none of them:

- **"in full"** — partial fills are recorded separately and are **not**
  counted as fills. A fill rate that mixes them measures two different things.
  `p_fill_partial` (any fill ≥ 1 contract) is reported alongside, and the two
  are never added.
- **"within its 300-second lifetime"** — a fill probability without a time
  bound is not a probability of anything. Change the lifetime and the number
  changes; it is a property of the policy as much as of the market.
- **"before the cancellation policy removes it"** — a quote we cancelled is a
  **completed observation with outcome NO_FILL**, not a dropout. Excluding
  cancelled quotes would condition on survival and inflate the estimate.
- **Denominator = quotes ACKNOWLEDGED by the venue**, not quotes sent.
  A rejected order never rested and cannot have filled.
- **Reported by spread bucket and by time-at-level**, because one pooled
  number across heterogeneous books is not a rate anyone can use.

### 5.2 Price movement: two distinct quantities, never added

| quantity | window | what it is |
|---|---|---|
| **quote-to-fill movement** | midpoint at quote → midpoint at fill | how the market moved **while we waited**. Selection on the quotes that filled. |
| **post-fill markout** | midpoint at fill → midpoint at fill + 60 s | adverse selection proper: what the fill told us we did not know. |

v1 collapsed these into one `conditional_reference_move`. They answer
different questions — the first is about the waiting, the second about the
counterparty — and the maker model takes **only the second**, once, as
`conditional_reference_move`. Adding both double-counts, which is the same
mixed-reference error that made the previous maker model report −0.015 on a
round trip whose cash P&L was zero.

### 5.3 Everything recorded

Not only fills. Every one of these is a row, and the count of each is a
reported figure:

| event | recorded |
|---|---|
| eligible quote opportunity **declined** | market, reason, book at decision |
| order sent | market, side, price, size, decision id, send timestamp |
| venue acknowledgement | ack timestamp, venue order id, **or the rejection and its reason** |
| resting | price, size, book at rest |
| partial fill | qty, price, fee charged, remainder still live |
| full fill | qty, price, fee charged |
| cancel requested | timestamp, unfilled remainder, still-reserved amount |
| cancel confirmed | timestamp, released amount, replacement placed or refused |
| **time at risk** | ack → flat, per contract, per market, summed |

A pilot that recorded only its fills could not compute a fill *rate*, because
the denominator would be missing.

### 5.4 Fees actually charged

From the settled statement, compared line by line against
`PMUS_PUBLISHED_2026_09_17`. **This is what moves the schedule's status from
`PUBLISHED` to `VERIFIED_APPLIED`,** and it also settles the one
disagreement between our two sources: whether the taker charge rounds per fill
independently (2026-07-01 documentation) or cumulatively across an order's
fills (2026-09-17 terms). Both are implemented; only a statement decides.

## 6. SAMPLE AND STOPPING — one protocol, not two

v1 gave different rules in §3 and §6: one said the pilot ends at *"14 days or
200 filled contracts, whichever first"*, the other that the hard stop is
*"14 days or $25 cumulative loss"* with 200 contracts as a target. **The
single protocol is:**

**STOP — the pilot ends when the first of these occurs:**

1. **200 contracts filled across ≥ 50 distinct contracts** — target reached.
2. **14 days elapsed** — time bound.
3. **`max_worst_case_loss` ($25) would be breached by the next order** —
   checked pre-trade, so the budget is never passed rather than detected
   after.

**HALT — quoting stops, open exposure is worked down, and the pilot does not
resume without a human decision:**

- `holds_both_legs_independently` changes;
- the execution gate becomes unreadable;
- freshness failures exceed 20% of decisions over 1 h;
- any reconciliation residual ≠ 0;
- a cancel is unacknowledged for > 60 s.

A HALT is not a STOP: it suspends new quotes and leaves the pilot's clock
running. If a HALT persists past 24 h it becomes a STOP.

**Early stop for success is NOT permitted.** Stopping when the number looks
good is how a null result becomes a positive one.

**Required quote count is itself unknown**, because it depends on the quantity
being measured: at an *assumed* 5% fill rate, 200 filled contracts at 5 per
quote is ~800 quotes; at 1% it is ~4,000. Hence the dual bound.

**What it establishes:** `p_fill` and post-fill markout for *this* account, at
*this* size, in *these* markets, over *this* window.

## 7. EXACT CHANGES AND ROLLBACK

**Decision-only deployment (no orders) — requested now:**

1. New worker `backend/sportsassets/workers/bettor_prospective.py`: loop on the
   existing authenticated read path, inject the reader into
   `bettor_prospective_runner.run(mode=PROSPECTIVE)`, persist records.
2. Migration: `bettor_prospective_decisions` table keyed by `observation_id`.
3. Render: add the worker to `sportsassets-workers`. **No new credential, no
   new permission, no order path.**
4. **Rollback:** remove the worker from the service. It writes only its own
   table and touches nothing else.

**Order-enabled pilot — NOT requested here:** additionally requires
`mirror_live` semantics for a new `pilot` lane, the lane added to the gate's
control set, and capital authorization. **Do not perform these on the strength
of this document.**

## 8. WHAT WOULD MAKE THIS PILOT WORTHLESS

Stated in advance so it cannot be rationalised afterwards:

- Fewer than 50 acknowledged quotes — no rate is estimable.
- `p_fill` confidence interval spanning more than [0.01, 0.30] — consistent
  with almost any economics.
- Fees on the statement disagreeing with the published schedule — the model's
  fee term was wrong and every figure built on it is superseded.
- Any reconciliation residual ≠ 0 — the accounting is untrustworthy and the
  measurement with it.

## 9. PREREQUISITES THAT COST NOTHING

| # | prerequisite | status |
|---|---|---|
| 1 | Published fee schedule resolved and wired | **DONE** — `PMUS_PUBLISHED_2026_09_17`, both dated schedules, 26 tests |
| 2 | Depth read as top-of-book, not the five-level sum | **DONE** — measured, corrected, tested |
| 3 | Freshness with required receipt timestamp, transport separated from skew | **DONE** |
| 4 | Combined exposure limit and pre-trade loss budget | **DONE** — enforced in the loop |
| 5 | Two-phase cancel so replacements never overlap | **DONE** |
| 6 | Run `capability_probe()` / `account_identity()` | **built; `bettor-capability-probe.yml` dispatches them read-only, key names and verdicts only** |
| 7 | **Wire `record_settlement()`** | **WRITTEN, NOT RUN** — `bettor_settlement_ingest`. The table is still empty because nothing has executed it, which is now a deployment question rather than a code one |
| 8 | Bound the opportunity from public data (§1) | **NOT DONE** |
| 9 | Verify the 11 two-label contracts as genuine complements | **PARTIAL** — the capture holds no venue outcome id and no settlement predicate, so this is checkable only against `instrument_id` / `condition_id` |

**All nine are read-only and none needs approval.** Items 7 and 9 in
particular mean we cannot currently measure settlement outcomes at all, and
cannot confirm from our own data that any observed pair settles under one
predicate to exactly one winner.
