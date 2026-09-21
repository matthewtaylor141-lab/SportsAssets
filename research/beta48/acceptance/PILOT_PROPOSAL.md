# MEASUREMENT PILOT — proposal for explicit approval

**This is a proposal. No order is activated by this document, and nothing in
the repository will submit one.** `mirror_live=false` and the execution gate
stay as they are until a separate authorization changes them.

**Its deliverable is a `p_fill` measurement, not profit.** A pilot that made
money and taught us nothing about fill probability would have failed.

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
`p_fill`. That work is read-only, costs nothing, and is listed in §8.

## 2. ACCOUNT, VENUE, FEES, ELIGIBLE MARKETS

| | |
|---|---|
| Venue | `polymarket-us` |
| Account | **institutional — IDENTITY UNVERIFIED.** `account_snapshot()` returns no wallet, address or account id. **`reconcile_read.capability_probe()` and `account_identity()` must run first**; a pilot on an account we cannot name is not a pilot. |
| Fee schedule | **MUST BE RESOLVED FROM THE PUBLISHED SCHEDULE BEFORE APPROVAL.** I previously said this needed a settled statement. That was wrong: the published maker/taker schedule is documentation, obtainable read-only. A settled statement verifies *application* later. |
| `holds_both_legs_independently` | **UNKNOWN.** Must be SUPPORTED or the pilot is single-leg only. |
| Eligible markets | Only contracts meeting **all**: `MARKET_STATE_OPEN`; two-sided book; spread ≥ 2 ticks; `DISPLAYED_DEPTH_AT_T0` present on the quoted side; source-clock freshness ≤ 10 s at decision; event start > 60 min away (avoid in-play volatility). |

## 3. SIZE AND EXPOSURE — worst case, not expected case

Every figure is computed from **the whole position filling immediately at the
worst price and resolving against us**, because that is the only bound that
does not depend on an unmeasured quantity.

| parameter | proposed | derivation |
|---|---|---|
| Quote size | **1 contract** | venue minimum; the smallest unit that can be filled at all |
| Max price | **≤ $0.50** | caps worst-case loss per contract at $0.50 |
| Simultaneous live quotes | **10** | one per market, ten markets |
| Max contracts held at once | **10** | one per market; no doubling |
| **Worst-case capital at risk** | **$5.00** | 10 × 1 × $0.50, all filled, all resolve worthless |
| Max cumulative loss (hard stop) | **$25.00** | five full wipeouts |
| Max unhedged directional exposure | **10 contracts / $5.00** | every fill is unhedged by construction |
| Duration | **14 days or 200 filled contracts, whichever first** | §6 |

These are deliberately near-trivial. **The experiment's value is the fill
record, and a 1-contract fill teaches as much about queue position as a
1,000-contract fill** — while a 1-contract fill at $0.445 earns a $0.00 rebate
after banker's rounding, which is itself a measurement worth having.

## 4. CONTROLS

- **Cancellation:** every quote carries a max resting time of 300 s, then
  cancel. Cancels are ungated by design — the execution gate never blocks them.
- **Partial fills:** the remainder stays reserved and live
  (`LIVE_QUOTE_STATES`); recovery policy runs on the filled portion.
- **One-leg exposure:** the existing ordered policy — COMPLETE if the
  complement is executable and the incremental cash beats the exit, else EXIT
  at the bid, else UNRESOLVED_EXPOSURE, named and escalated. Requires a fresh
  recovery read; a stale one refuses.
- **Automatic stops, any one halts all quoting:** cumulative loss ≥ $25;
  unhedged exposure > 10 contracts; `holds_both_legs_independently` changing;
  execution gate unreadable; freshness failures > 20% of decisions over 1 h;
  any reconciliation residual ≠ 0.
- **Every order passes the execution gate** (`authorize("submit", lane=...)`),
  which fails closed on an unreadable kill switch.

## 5. WHAT IS MEASURED

| quantity | how |
|---|---|
| **`p_fill`** | filled quotes ÷ quotes rested, by spread bucket, by time-at-level |
| **Adverse selection** | `conditional_reference_move`: venue midpoint at fill+60 s minus midpoint at quote, conditional on our fill |
| **Fees actually charged** | from the settled statement, against the published schedule — this is where application is verified |
| **Inventory duration** | fill timestamp → flat timestamp |
| **Exit economics** | realised exit price vs the exit route the model assumed |

Each feeds one term of the corrected model:
`round_trip = (exit − entry) × qty − fees + rebates − carry`.

## 6. SAMPLE, STOPPING, AND WHAT IT CANNOT ESTABLISH

**Sample:** 200 filled contracts across ≥ 50 distinct contracts. At an
*assumed* 5% fill rate that is ~4,000 quotes; at 1% it is ~20,000. **The
required quote count is itself unknown, because it depends on the quantity
being measured.** Hence the dual stop.

**Stopping rules:** hard stop at 14 days or $25 cumulative loss, whichever
first. **Early stop for success is NOT permitted** — stopping when the number
looks good is how a null result becomes a positive one.

**What it establishes:** `p_fill` and post-fill markout for *this* account, at
*this* size, in *these* markets, over *this* window.

**What it cannot establish:** that `p_fill` at 1 contract equals `p_fill` at
100 — a larger order sits differently in the queue and may move the market it
is measuring. Scaling from this pilot to $500k/day is **not** licensed by it,
and §7 of the capacity analysis must be redone with pilot data rather than
extrapolated from it.

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

## 8. PREREQUISITES THAT COST NOTHING AND ARE NOT DONE

1. Run `capability_probe()` / `account_identity()` — built, unrun.
2. Resolve the published fee schedule.
3. **Wire `record_settlement()`** — it is defined and *never called*, which is
   why `bettor_state_settlements` is empty.
4. Bound the opportunity from public data (§1).
5. Verify the 11 two-label contracts as genuine complements — now testable
   with the corrected `pair_legs()`.

**All five are read-only and none needs approval. They are the honest
prerequisite to asking for capital, and item 3 in particular means we cannot
currently measure settlement outcomes at all.**
