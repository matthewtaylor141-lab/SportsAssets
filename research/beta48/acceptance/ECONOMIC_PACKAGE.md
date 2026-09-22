# BETTOR — the consolidated economic package

**The investment decision: do not deploy capital on the two-sided
maker policy. Rebuild the pilot around a hard-flatten variant, whose
required coverage is 44 events rather than 63,709, and re-decide on
that measurement.**

Every number below comes from running the production decision engine
(`bettor_policy_ev.incremental_ev`) over the time-ordered tape. There
is no spreadsheet.

---

## 0. What changed, and why the earlier answer was wrong

The previous package reported the two-sided maker policy as the
strongest survivor: **+0.0086 per contract, positive in 1,551 of 1,551
observations.**

That was **one branch of an identity, evaluated once per snapshot.**
It priced the world in which both legs fill and stopped there. It
never asked how often both legs fill, and it never carried the
inventory from the episodes where they did not.

Run as whole episodes:

| | |
|---|---|
| **per contract, cluster-aware** | **−0.0291**, 95% CI [−0.1402, +0.0820] |
| episodes | 943, over 11 markets / 11 events |
| total | **−223.20** on 100-contract quotes |
| positive | 149 of 943 |
| double-fill rate **q**, measured | **0.0753** against a 0.163 break-even |

The favourable branch happens **7.5%** of the time. It was being
priced as though it happened always.

---

## 1. The executable policy

`research/beta48/bettor_episodes.py`. Not a description of a policy —
the thing that was run.

**BEGINS** at an observation where the market is OPEN, the book is
two-sided and the spread is at least one tick. Two resting orders go
up, both priced *by the engine*:

```
YES leg   buy YES at incremental_ev("QUOTE_BID").terms["quote_price"]
NO  leg   buy NO  at 1 − incremental_ev("QUOTE_OFFER").terms["quote_price"]
```

The NO leg **is** our YES offer. PMUS carries `shortQuote = 1 − bestBid`,
so the NO book is the exact mirror of the YES book. Modelling it as two
long legs keeps the cash unambiguous: one YES plus one NO pays exactly
1 at settlement, whatever happens.

**PARTIAL FILLS** — each leg fills in pieces. Every fill is its own
event with its own size, its own price and **its own banker-rounded
fee**. 581 of 958 realised fills were below the order size.

**CANCELLATION ACKNOWLEDGEMENT** — at the quoting horizon the unfilled
legs are cancelled. The cancel is **not** effective when requested: it
takes effect from the next observation, so any print in the interval
containing the request still fills us. The race is charged against us
every time. (It never actually bit in this corpus: **0 episodes** had a
fill after the cancel request. That is a fact about this tape, not a
reason to remove the modelling — PMUS carries `ORDER_STATE_PENDING_CANCEL`
as a real state.)

**RECOVERY POLICY** — unmatched inventory is not abandoned. The policy
rests a maker exit on the opposite side for `RECOVERY_WAIT`
observations; if that does not fill, it crosses out as a taker. Matched
pairs are left to settle: they pay exactly 1, and completing them early
can only cost fees.

**ENDS** at the first of `FLAT_PAIRED`, `FLAT_EXITED_MAKER`,
`FLAT_EXITED_TAKER`, `CARRIED_SETTLED`, `CARRIED_OPEN_AT_HORIZON`,
`NEVER_FILLED`.

### Assumptions, named rather than buried

| | |
|---|---|
| **A1 prints** | `sharesTraded` is cumulative; its delta is the interval's volume. `lastTradePx` gives *one* price for that interval. Multi-price intervals are attributed to that single price. Cuts both ways; not fixable from a BBO feed. |
| **A2 queue** | Inside the spread → front of queue. At the touch → behind the observed depth. A third variant assumes behind-the-depth always. Both reported. |
| **A3 our quote is not in the tape** | The recorded book never saw our order. A resting quote inside the spread would have changed what others did. Not fixable from recorded data — only by resting real orders. |
| **A4 rebates unverified** | Everything computed twice: published maker schedule applied, and every rebate zeroed. |

---

## 2. Episode accounting — realized cash, marked inventory, settlement

943 episodes, size 100, published rebates, front-of-queue:

| end state | n | sum | mean |
|---|---:|---:|---:|
| `FLAT_PAIRED` | 71 | **+46.77** | +0.66 |
| `FLAT_EXITED_TAKER` | 280 | −150.45 | −0.54 |
| `FLAT_EXITED_MAKER` | 46 | −51.84 | −1.13 |
| `CARRIED_SETTLED` | 6 | **−66.46** | **−11.08** |
| `CARRIED_OPEN_AT_HORIZON` | 2 | −1.22 | −0.61 |
| `NEVER_FILLED` | 538 | 0.00 | 0.00 |
| | **943** | **−223.20** | **−0.24** |

**One carried episode wipes out seventeen paired ones.** Those six
episodes were dropped from every number this programme had produced
until now. They are the result.

The three quantities are kept apart on every episode:

| | |
|---|---|
| `realised_cash` | actual cash in and out, fees included |
| `residual_contracts` | yes / no / matched pairs / net directional |
| `residual_value` + `residual_basis` | valued at **observed settlement** where the market expired in-sample; at **the touch we could actually hit, net of the taker fee** where the capture ended first; at **1.0 per matched pair** because that is certain |

The six carried episodes, individually:

```
market                            status            realised   residual    total
aec-cfb-coast-del-2026-09-19      CARRIED_SETTLED    -33.720     +0.000   -33.720
aec-cfb-kentst-ohiost-2026-09-19  CARRIED_SETTLED     -0.490     +0.000    -0.490
aec-nfl-atl-pit-2026-09-13        CARRIED_SETTLED    -71.750   +100.000   +28.250
aec-nfl-bal-ind-2026-09-13        CARRIED_SETTLED    -40.200     +0.000   -40.200
aec-nfl-cle-jax-2026-09-13        CARRIED_SETTLED    -20.300     +0.000   -20.300
atc-lmx-pue-tol-2026-09-04-draw   CARRIED_SETTLED     +0.000     +0.000    +0.000
aec-boxing-...-2026-10-31         CARRIED_OPEN       -16.665    +15.447    -1.217
atc-lmx-ame-tij-2026-09-05-tij    CARRIED_OPEN        +0.000     +0.000    +0.000
```

Where the money actually goes: **taker fees −94.44 against rebates
+46.13.** Crossing out unmatched inventory costs more than twice what
every maker rebate in the run earns.

### Execution sensitivity — negative everywhere

| setting | episodes | q | sum | per contract |
|---|---:|---:|---:|---:|
| horizon 5, size 4 | 2,549 | 0.0114 | −11.61 | −0.001139 |
| horizon 5, size 100 | 2,527 | 0.0055 | −195.61 | −0.000774 |
| horizon 10, size 4 | 1,595 | 0.0426 | −14.74 | −0.002311 |
| horizon 10, size 100 | 1,570 | 0.0248 | −238.93 | −0.001522 |
| horizon 20, size 4 | 970 | 0.1165 | −12.18 | −0.003140 |
| horizon 20, size 100 | 943 | 0.0753 | −223.20 | −0.002367 |
| horizon 40, size 4 | 602 | 0.2558 | −9.95 | −0.004132 |
| horizon 40, size 100 | 562 | 0.1744 | −96.50 | −0.001717 |

**q and the conditional return move together.** Resting longer raises
q from 0.0055 to 0.1744 — past the 0.163 break-even — and the result
stays negative at every one of eight settings. The "q > q\* and it
earns" framing was itself the over-simplified model, for exactly the
reason flagged earlier: quote price, size and waiting time move the
fill probability and the conditional return at the same time.

Rebates excluded: −269.33 (−0.030535/contract). Queue-behind-always:
−221.98, q 0.0721. **The sign does not depend on either.**

---

## 3. Exact fee and rebate arithmetic

"One or two contracts round the rebate to zero" was **not universally
true**, and the error was mine. `fee = Θ·C·p·(1−p)`, banker-rounded to
the cent **per fill**, Θ_maker = −0.0125:

| price | raw @1 | n=1 | n=2 | n=4 | n=10 | n=100 | n=1000 | **min n for any rebate** |
|---|---|---|---|---|---|---|---|---|
| 0.005 | 0.0000622 | 0 | 0 | 0 | 0 | 0.000100 | 0.000060 | **81** |
| 0.010 | 0.0001238 | 0 | 0 | 0 | 0 | 0.000100 | 0.000120 | **41** |
| 0.020 | 0.0002450 | 0 | 0 | 0 | 0 | 0.000200 | 0.000240 | **21** |
| 0.050 | 0.0005938 | 0 | 0 | 0 | 0.001000 | 0.000600 | 0.000590 | **9** |
| 0.100 | 0.0011250 | 0 | 0 | 0 | 0.001000 | 0.001100 | 0.001120 | **5** |
| 0.130 | 0.0014138 | 0 | 0 | 0.002500 | 0.001000 | 0.001400 | 0.001410 | **4** |
| 0.200 | 0.0020000 | 0 | 0 | 0.002500 | 0.002000 | 0.002000 | 0.002000 | **3** |
| 0.300 | 0.0026250 | 0 | **0.005000** | 0.002500 | 0.003000 | 0.002600 | 0.002620 | **2** |
| 0.500 | 0.0031250 | 0 | **0.005000** | 0.002500 | 0.003000 | 0.003100 | 0.003120 | **2** |
| 0.730 | 0.0024638 | 0 | 0 | 0.002500 | 0.002000 | 0.002500 | 0.002460 | **3** |
| 0.950 | 0.0005938 | 0 | 0 | 0 | 0.001000 | 0.000600 | 0.000590 | **9** |

Three things the blanket claim got wrong:

1. **The minimum fill for a non-zero rebate ranges from 2 to 81**,
   depending entirely on price. "Four contracts" was true at p=0.13 and
   nowhere else.
2. **The per-contract rebate is non-monotonic in size.** At p=0.13 a
   4-contract fill earns 0.0025/contract, a 10-contract fill earns
   0.0010, a 100-contract fill earns 0.0014. Rounding makes it jagged,
   not increasing.
3. **At p = 0.30–0.60 a two-contract fill earns 0.005/contract** — the
   best per-contract rate in the whole table. The claim was not merely
   imprecise; at those prices it was backwards.

**And the order size is not the fill size.** Realised fills in the run:

```
fills 958   median 63.0   mean 59.3   partial (below order size) 581
buckets  1:21   2:24   3-4:27   5-9:90   10-99:419   >=100:377
```

An order for four contracts that fills 1+1+1+1 pays four separately
rounded fees — **zero** at every price below 0.30. Every number in this
package applies the fee per fill event, on the realised size.

**With rebates excluded entirely**, the policy result moves from
−223.20 to −269.33. The rebate is worth 46.13 across 943 episodes and
does not change the sign. *The policy does not depend on an unverified
rebate; it fails with or without one.*

---

## 4. Case-study profit attribution

`research/beta48/bettor_rn1_attribution.py`, over 9,542 resolved
conditions (8,210 of 17,752 had no resolved settlement in the snapshot
and are excluded — **46% unaccounted**, stated because it bounds what
follows).

Per condition: net position and average cost per outcome; matched =
min over outcomes; **pair channel** = matched × (1 − Σ average costs);
**settlement channel** = residual × (payout − average cost).

| | capital | profit |
|---|---:|---:|
| pair channel (matched) | 14,558,910 | **310,254** |
| settlement (residual) | 10,468,652 | **165,479** |
| **total** | **25,027,562** | **475,734** |
| **share** | pair **58.2%** / settle **41.8%** | pair **65.2%** / settle **34.8%** |

**The two shares are different numbers, and I had the conclusion
backwards.** The earlier package inferred from "43.9%–91.9% of capital
is held to settlement" that hold-to-settlement "explains most of the
returns." Computed properly, the **pair channel is the larger profit
contributor** — 65.2% of profit on 58.2% of capital. A capital share
was being read as a profit share, which is exactly the error that was
called out.

Two further facts that bound what this evidence supports:

- **Per-condition total P&L: mean +$49.86, SE $27.68, 95% CI
  [−$4.40, +$104.11].** It **crosses zero** — and that is the *naive*
  interval, which is a floor, because conditions within one event and
  one day are not independent. On 9,542 conditions this account's edge
  is still not statistically demonstrated.
- The channels are not uniformly positive: Tennis settlement −17,406,
  Non-Sports pair −8,376. Soccer alone carries 64% of the total.

**Attribution convention, stated because it is a choice:** average cost
per outcome, which splits profit in proportion to size. FIFO matching
would move profit between channels without changing the total. The
total is convention-free; the split is not.

---

## 5. The coverage table, repaired

| # | mechanism | verdict | what changed |
|---|---|---|---|
| 1 | Informed entry + hold to settlement | **UNRESOLVED — now partially measurable** | E[settlement−price] is no longer NOT_MEASURED. See §6. |
| 2 | Taker complementary pair | **REJECTED** | unchanged; `ask + (1−bid) = 1 + spread` is an identity |
| 3 | Maker entry + taker completion | **REJECTED on these books, as a STATIC calculation only** | see the correction below |
| 4 | Maker entry + cross out | **REJECTED as a policy** | it is the hurdle, not a strategy |
| 5 | Rest on BOTH sides | **REJECTED on development evidence** ← *was "UNRESOLVED, the strongest survivor"* | −0.0291/contract over whole episodes; q = 0.0753 measured against a 0.163 break-even |
| 6 | Cross-venue PMUS/Kalshi (Track P) | **REJECTED, LOCKED** | unchanged; preserved as a negative result, not to be retuned |
| 7 | Copy a profitable account | **REJECTED — for a better reason** | see the correction below |
| 8 | Feasible netting / capital release | **UNRESOLVED — API surface now audited** | see §8 |
| 9 | Adverse-selection–aware maker | **UNRESOLVED, not implemented** | unchanged |

### The four specific repairs

**(a) "Capital held to settlement" ≠ "profit earned from settlement."**
Repaired in §4. The pair channel earns 65.2% of the profit. My earlier
row-1 claim — "the headline returns are mostly this row, not the pair
channel" — was inferred from a capital share and is **withdrawn**.

**(b) Three positive and three negative pair-channel results do not
reject every account-copying policy.** Correct — a 3–3 sign split is
weak evidence about *any* copy policy, and I over-read it. The copy
policy is still rejected, but on two measurements that actually bear
on it:

- **The signal is already priced when it reaches us.** Median
  `best_ask_at_detection − their_fill_price` = **+0.0100**, mean
  +0.0232. In **89.5%** of 214,609 fills the ask had already moved past
  their price. The entire two-sided maker edge is one half-spread —
  0.0025 at a one-cent book. **The copy slippage is four times it.**
- **The markets are not on our venue.** PMUS lists under `aec`/`atc`/
  `asc`/`tec`. These fills are global-CLOB slugs — `itf`, `atp`, `wta`,
  `cs2`, `lal`, `ucl`, `epl`. **0 of 214,609 fills sit in a PMUS slug
  namespace**, and no market-level linkage between the two venues has
  ever been established in this repository.

A signal we cannot execute, arriving after the price has moved four
times our edge against us, is rejected on execution grounds — not on a
3–3 count.

**(c) An unchanged-book calculation does not reject every
time-dependent exit or completion policy.** Correct, and this is now
addressed directly rather than conceded. The episode engine **is** a
time-dependent exit policy: it rests, waits, cancels with an
acknowledgement delay, rests a maker exit for `RECOVERY_WAIT`
observations, and only then crosses out. Row 3's verdict is therefore
narrowed to what was actually tested: **a static one-book calculation
rejects the static version.** The time-dependent version was run over
eight horizon/size settings and is negative at all eight — which is a
much stronger claim than the static one, and is the one that now
carries the row.

What remains genuinely untested: exits conditioned on a **signal**
(price movement, volume, time-to-event) rather than on a clock. Nothing
here rejects those, and nothing here supports them either.

**(d) 1,551 observations were never 1,551 episodes.** They were 1,551
*snapshots* after deduplication and cohort filtering, on **11 distinct
markets across 11 distinct events**. The episode run reports the
honest units throughout: **943 episodes, 11 markets, 11 events**, and
every interval is computed on the **11 event clusters**, not on the
episodes.

The cost of having got this wrong is measurable: **the naive standard
error is 0.001035 and the cluster standard error is 0.049866 — a factor
of 48.** An interval built on episodes would have reported −0.0291 ±
0.002, a decisive and entirely spurious result.

---

## 6. E[settlement − price] — no longer unmeasured

Every prior document recorded estimand A as **NOT YET MEASURED**. It is
measurable, and the reason it was missed is worth recording because it
is also a trap.

`settlementPx` is present on **open** rows and is **not** the outcome.
It is constant for a market's whole open life and only moves after
expiry, sometimes hours later:

```
aec-cfb-coast-del   settlementPx 0.37 for all 4,640 OPEN rows,
                    0.37 for the first 5 EXPIRED rows, then 0.0.
                    The market traded down to 0.03/0.05 and settled ZERO.
aec-nfl-chi-car     0.60 while open, 0.60 for 5 EXPIRED rows at 20:38,
                    then 1.0 from 04:06 the next morning.
```

Reading it off an open row would have been a lookahead bug **that also
happened to be wrong** — 0.37 against a true 0.0. The realised outcome
is the value on the last expired row, and only once it has moved off
the open-state value. `bettor_tape.settlement_label()` enforces exactly
that and returns `SETTLEMENT_NOT_OBSERVED` otherwise.

**Ten markets labelled, on nine events with a two-sided book:**

| | point | 95% CI (9 clusters) |
|---|---|---|
| buy YES at the ask, hold | **−0.0532** | [−0.2689, +0.1625] |
| buy NO at 1−bid, hold | **+0.0454** | [−0.1690, +0.2597] |

Eight of ten settled at 0; two at 1. **The last mid called the outcome
correctly in 9 of 9.** The positive NO-side point estimate is the
classic longshot-bias signature and it is driven entirely by the class
balance: eight zeros. Both intervals span zero by a wide margin. **This
supports nothing. It is now a measured quantity with a named
uncertainty instead of a blank.**

---

## 7. Fair value: the replacement hypothesis, and why it is not yet testable

The directive asked to turn the missing fair-value capability into
executable work. Three steps, honestly reported:

**Trace the entries to contemporaneous information.** Done, §5(b). The
information the case study acted on reaches us after the price has
moved a median full cent against us, in markets we cannot trade.

**Identify reproducible signals.** From our own venue, the observation
probe and the tape together give: the touch, both depths, cumulative
`sharesTraded`, `lastTradePx`, `openInterest`, `currentPx`, and the
static `settlementPx` reference. The one candidate with any
cross-sectional structure is the **longshot-bias hypothesis** —
`E[settlement | mid] < mid` for low-priced outcomes.

**Build and test it.** It cannot be tested here, and saying so is the
honest answer rather than a failure to try:

- **n = 9 events with 8 zeros.** The hypothesis and the class balance
  are the same fact in this corpus. Any fit is unfalsifiable.
- **The labels are now development data.** They were read for the first
  time in this package, which makes them development data from that
  moment. There is no untouched PMUS holdout, and relabelling these as
  one is precisely what was forbidden.
- The prior challenger evaluation returned `NOT_DETECTED` at the
  available sample size, and nothing here changes the sample size.

**`FV_BETTOR_INDEPENDENT` remains NOT_IDENTIFIED**, and the engine's
refusal to price `CROSS_BUY` and `HOLD` without it remains correct. The
executable work is specified in §9 as a prospective design; it is not
claimed as a result.

---

## 8. Capital — the $250 limit, defined

The venue's API surface was audited (`polymarket_us` SDK, read from the
installed package). **What the fields establish and what they do not is
kept apart.**

### Capability: what exists

| | |
|---|---|
| sell an owned long | `ORDER_INTENT_SELL_LONG`, plus `/v1/order/close-position` |
| short directly | `ORDER_INTENT_BUY_SHORT` / `SELL_SHORT` — the venue models a short natively, not only via the complement |
| cancel | `/v1/order/{id}/cancel`, `/v1/orders/open/cancel`, `/v1/order/{id}/modify` |
| **the cancellation race is a real venue state** | `ORDER_STATE_PENDING_CANCEL` and `ORDER_STATE_PENDING_REPLACE` are declared order states |
| position detail | `UserPosition`: `netPosition`, **`qtyAvailable`** (a *separate* field), `cost`, `realized`, `cashValue`, `expired` |
| balance detail | `UserBalance`: `currentBalance`, `buyingPower`, `assetNotional`, `assetAvailable`, **`openOrders`**, **`unsettledFunds`**, `marginRequirement`, `balanceReservation` |

Two of these bear directly on the claim that capital recycles
immediately, and both point the other way:

- **`openOrders` is a balance line.** A venue that reports resting
  orders as a balance component is almost certainly encumbering buying
  power for them.
- **`unsettledFunds` is a separate balance line from `currentBalance`.**
  Proceeds do not become immediately redeployable cash.
- **`qtyAvailable` is separate from `netPosition`.** Some owned shares
  can be unavailable to sell.

**None of these values has been read from a live account.** The field
names establish that the venue *tracks* these quantities; they
establish nothing about magnitudes or release timing. **The claim that
"buy-at-bid / sell-at-ask releases capital immediately" is
WITHDRAWN** — it was never verified, and the API surface suggests it is
wrong.

### The $250 limit, separated into the four quantities

| quantity | definition | who enforces it |
|---|---|---|
| **committed collateral** | cash the venue is holding: filled legs at cost **plus**, if `openOrders` encumbers, the unfilled resting size at its limit price. For a two-sided quote of size S: `(p_bid + 1 − p_offer)·S = (1 − captured_spread)·S`, i.e. **just under S dollars per contract-pair even with zero fills** | the venue; we measure it by reading `UserBalance.openOrders` before and after |
| **inventory exposure** | worst-case loss on unmatched inventory if it settles against us: `excess_yes × avg_cost + excess_no × avg_cost`. Matched pairs have **zero** exposure — they pay exactly 1 | our own position tracking |
| **realised losses** | cumulative `realised_cash` across closed episodes. Cash already gone | the ledger |
| **worst-case remaining loss** | realised losses **+** inventory exposure **+** the cost of the pending-order race: every resting order that could still fill between a cancel request and its acknowledgement, valued at its full limit price | computed before every quote goes up |

**The hard cap is on the fourth quantity, not the first or third.**
`worst_case_remaining_loss ≤ $250` must hold *at the instant a new
order is submitted*, with the pending-order race counted as though
every resting order fills. A cap on realised losses alone permits an
unbounded position to be standing when the cap is hit.

**Enforced at the order path, not by policy.** No order is submitted
unless the pre-trade check passes; this cannot be a post-hoc
reconciliation.

### What must be verified before any capital moves

1. Read `UserBalance` before and after placing one resting order, and
   confirm whether `openOrders` and `buyingPower` move. **Until this is
   measured, assume resting orders fully encumber.**
2. Read `UserPosition.qtyAvailable` against `netPosition` after a fill.
3. Observe one matched pair and determine whether the venue nets it and
   **when** the released cash leaves `unsettledFunds`.
4. Measure the cancel→acknowledgement latency and whether a fill can
   land inside it.

All four are read-only or single-order observations. None is a
profitability test.

---

## 9. The frozen evaluation protocol

### The stopping rule was invalid and is replaced

"Stop when the interval excludes zero," checked after every episode, is
**repeated significance testing on an ordinary confidence interval**.
Under the null it reaches exclusion with probability approaching 1. The
previous proposal would have produced a "significant" result on noise.

**Replacement — pre-registered, fixed endpoint, event-level:**

| | |
|---|---|
| **unit of analysis** | the **EVENT**, not the episode, not the market. One summary statistic per event: mean net cash per contract over that event's episodes |
| **primary metric** | net cash per contract, realised **plus** carried residual valued as in §2. Not a mixture proportion, not a settlement return, not `q` |
| **endpoint** | **fixed at N events, pre-registered before collection.** No interim looks drive the decision |
| **test** | one-sample t on the N event-level means, two-sided |
| **alpha** | **0.05 / 17 = 0.00294**, Bonferroni over the 17 variants this programme has actually tried (4 policy grid cells + 8 sensitivity cells + 5 mechanisms). The count is in `bettor_power.py` so it cannot be quietly revised |
| **power** | 80% |
| **interim monitoring** | permitted **only** for the loss cap and for operational faults. If continuous statistical monitoring is ever wanted, it must use an always-valid confidence sequence (a test martingale / e-value), not a t-interval |

### Detectable edge and required coverage — computed

`research/beta48/bettor_power.py`. σ is the standard deviation across
**event-level** mean net cash per contract, measured from the episode
run.

| policy shape | σ across events | events to detect **+0.0025/contract** at 80%, Bonferroni |
|---|---:|---:|
| **AS RUN** (inventory may carry to expiry) | **0.1654** | **63,709** |
| **HARD FLATTEN** (never carry to expiry) | **0.004333** | **44** |

**This is the most actionable finding in the package. The settlement
tail, not the edge, is what makes the experiment impossible.** Carrying
inventory through expiry admits ±0.40-per-contract outcomes into a
distribution whose signal is 0.0025. Removing the carry shrinks σ by a
factor of **38** and the required coverage by a factor of **1,448**.

### What 500 episodes was actually worth

```
this corpus averaged           86 episodes per event
500 episodes  ~=               5.8 events
Bonferroni MDE at 5.8 events   +0.2613 per contract
the edge being hunted           +0.0025 per contract
                               ---------------------
                               105x too coarse to see it
```

**500 was a cap, never a power calculation.** At that coverage the
design could not have detected an edge a hundred times larger than the
one it was built to find.

---

## 10. The remaining measurements, exactly

| # | measurement | needs | cost | resolves |
|---|---|---|---|---|
| **M1** | Does a resting order encumber `buyingPower`? Read `UserBalance` before/after one order | order path, ~$4 | minutes | §8 items 1–2; the collateral term in the $250 cap |
| **M2** | Cancel→ack latency, and whether a fill lands inside it | order path | minutes | the race term in the $250 cap |
| **M3** | Netting and release: when does a matched pair leave `unsettledFunds`? | order path, one pair | one settlement cycle | mechanism 8, and all capacity arithmetic |
| **M4** | **Net cash per event, HARD-FLATTEN two-sided maker, 44 events** | order path, capital | 44 events of market time | **the investment decision on mechanism 5** |
| **M5** | E[settlement − price] on untouched prospective data | observation only, no capital | market time | mechanism 1, and `FV_BETTOR_INDEPENDENT` |

**M1–M3 are the ones that should happen first.** They are single-order
observations, they cost a few dollars, they are not profitability
tests, and **M4's loss cap cannot be defined correctly without them.**

M5 needs **no capital at all** — it needs the observation probe to run
long enough to see markets settle. The probe's current shape cannot do
that: enrichment consumes the entire 40-market distinct budget before
the stream opens, so an 1,800-second authorization produced 30 seconds
of streaming. That is a fixable budget-shape defect, documented in the
probe report.

---

## 11. Against the mandate

| | |
|---|---|
| **$500,000/day** | **NOT SUPPORTED.** Unchanged, and now for a stronger reason than measured opportunity: no policy in the engine has a positive point estimate on development data |
| **supported scale today** | **zero** |
| **strongest remaining policy** | HARD-FLATTEN two-sided maker — not because it is profitable, but because it is the only candidate whose **measurement is affordable** (44 events instead of 63,709) |
| **its development point estimate** | **negative**, on 8 of 11 event clusters |
| **recommended action** | run M1–M3 (single orders, a few dollars). **Do not fund M4 on this evidence.** Reconsider M4 only if M1–M3 show the collateral and release mechanics are materially better than the API surface suggests |

**This is a negative result, reported as one.** The primary mandate — a
BETTOR-native autonomous EV and decision engine with a defensible path
to $500,000/day of profitable turnover — **remains incomplete.** No
policy this programme has built is supported for capital, and the one
that looked strongest in the previous package is, measured properly,
negative.

---

## 12. Reproducing every number

```bash
# the tape: 30,590 bodies, 12 markets, 7 days, settlement labels
python research/beta48/bettor_tape.py

# episodes, fee arithmetic, settlement returns, sensitivity
python research/beta48/bettor_episode_report.py

# detectable edge and required coverage
python research/beta48/bettor_power.py

# case-study profit attribution
python research/beta48/bettor_rn1_attribution.py

# is the copy signal tradeable, and is it on our venue?
python research/beta48/bettor_copy_signal.py

# the engine's own unit tests
python -m pytest research/beta48/test_bettor_policy_ev.py -q
```

Results land in `research/beta48/acceptance/`: `episode_results.json`,
`power.json`, `rn1_attribution.json`, `copy_signal.json`.

### Data status

| dataset | identity | status |
|---|---|---|
| PMUS capture | `research/evidence/capture/run85_phase2_segment_*` — 27 segments, 30,590 bodies, 12 markets, 2026-09-13 → 2026-09-20 | **DEVELOPMENT** |
| PMUS settlement labels | the same bodies' post-expiry `settlementPx`, 10 markets | **DEVELOPMENT** — read for the first time in this package, and development data from that moment |
| Case-study fills | `research/snapshots/u2_events_v1.jsonl.gz`, 214,609 fills | **DEVELOPMENT**, global CLOB |
| Case-study settlements | `research/snapshots/settlement_v1.jsonl`, 17,752 conditions (9,542 resolved) | **DEVELOPMENT** |
| Track P holdout | `research/evidence/phasex*`, 1,170 markets | **SPENT** — locked negative |

**There is no untouched PMUS holdout.** Every out-of-sample path is
prospective. Nothing in this package is presented as a holdout result
and no previously-examined observation is relabelled as one.
