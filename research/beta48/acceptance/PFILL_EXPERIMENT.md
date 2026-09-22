# The smallest experiment that resolves P_FILL

**Status: SPECIFIED, NOT AUTHORIZED.** Preparing this is authorized;
placing orders is not. Nothing in this document has been run.

---

## Why an experiment is the only route

`bettor_decision_engine` refuses to score `MAKE_YES` / `MAKE_NO` because
`P_FILL` is `NOT_IDENTIFIED`, and `NOT_IDENTIFIED` is not zero. That is
not a conservative setting to be relaxed — it is a required input that
does not exist.

It cannot be supplied by observation, and the reason is structural: a
public feed shows the book, never *our order in it*. Three quantities
are invisible from outside and all three are needed:

1. **Queue position.** The venue publishes aggregate size per level, not
   per-order queues. The replay sweeps a queue-ahead fraction across
   {0.00, 0.25, 0.50, 1.00} precisely because it cannot know one, and
   every fill figure inherits that sweep.
2. **Whether a trade would have hit us.** Time-and-sales carries price,
   time and quantity — no side, no aggressor flag, no counterparty.
3. **Our own market impact.** A resting quote inside the spread changes
   what others do. The recorded book never saw our order.

The measured consequence: on the development corpus, **85 % of episodes
never filled**, and the entire measured P&L — positive and negative —
came from the 15 % that did. Every economic number therefore rests on an
unmeasured quantity.

---

## What the experiment measures, and nothing else

> **Given a resting quote at a known price, placed at a known time, on a
> known book: did it fill, how much of it, and how long did it take?**

That is the whole objective. Explicitly **not** measured in this
experiment: profitability, strategy validation, inventory policy, or net
economics. Those need the fill model this produces, plus a separate
evaluation.

### Recorded per order

| field | why it is needed |
|---|---|
| placement time, price, size | the decision instant |
| full ladder at placement | depth ahead at our price and better |
| aggregate size at our price, before and after | our position within the level |
| every partial fill: time, size, price | partial fills are the normal case, not the exception |
| cancel time and size remaining | a cancel is an outcome, not a missing row |
| trailing flow and realised volatility at placement | so the fill model can condition on decision-time inputs the live engine also has |
| time to first fill, time to full fill | the hazard, not just the rate |

The output is a **fill hazard conditioned on decision-time observables**
— which is exactly the shape `bettor_p_fill` expects and currently
refuses to produce.

---

## The recommended numbers

These are derived, not chosen. The derivation is below each one.

| parameter | recommended | binds because |
|---|---|---|
| **clip** | **5 contracts per side** | see the correction below — it does NOT guarantee a rebate |
| **price band** | **mid ∈ [0.20, 0.80]** | bounds the limit price, hence the ceiling |
| **market set** | **6 markets, selected by measured depth** — see below | fills must be reachable |
| **max concurrent two-sided quotes** | **6** (12 resting orders) | committed collateral + the pending race |
| **hard contract cap** | **200 filled contracts**, both sides, whole experiment | the $250 cap |
| **worst-case cash at risk** | **$210.22** | see the corrected arithmetic below |
| **pre-trade cap** | **worst_case_remaining_loss ≤ $250**, unchanged | ECONOMIC_PACKAGE §8 |
| **wall clock** | one fixed 6-hour window, no extension, no replacement | — |

### CORRECTION 1 — order size does not buy a rebate; FILL PIECES do

My earlier reasoning here was wrong and the error mattered. The fee is
banker-rounded to the cent **per fill**, not per order, and an order
fills in however many pieces the book gives it. The same 5-contract
order at p = 0.50:

| fill granularity | rebate per piece | **total** |
|---|---|---|
| one piece of 5 | $0.015625 → 2¢ | **$0.02** |
| two pieces of 2.5 | $0.007812 → 1¢ | **$0.02** |
| **five pieces of 1** | $0.003125 → **0¢** | **$0.00** |

Same order, same price, same size — and the rebate is either two cents
or **nothing**, decided entirely by how the counterparty flow arrives.
The threshold is on the **piece**: `k ≥ 0.4 / p(1−p)`, so ≥ 1.6
contracts per piece at p = 0.50 and ≥ 2.5 at p = 0.20.

**We do not control piece size.** So the maker rebate is not a
dependable component of the economics at this scale — it is a lottery
on fill granularity, and at a 1-contract granularity it is exactly
zero. This *strengthens* the measured finding rather than softening it:
the taker side rounds up reliably at these sizes while the maker side
can round away to nothing.

**Consequence for the experiment:** fill-piece-size distribution becomes
a *primary* output, not a detail. It determines whether a maker rebate
exists at all, and nothing we already hold measures it.

### CORRECTION 2 — the ceiling must include fees, the pending race, and accumulated losses

My $240 figure counted only premium at the band edge. The $250 cap in
ECONOMIC_PACKAGE §8 is `realised losses + inventory exposure + the
pending-order race`, so the ceiling has to carry all three:

```
worst = N × P_max  +  N × taker_fee_at_P_max  +  pending_race
      = N × 0.80   +  N × $0.01112            +  (12 orders × 5 × 0.80 = $48)
```

| N | worst case | |
|---|---|---|
| 300 | **$291.34** | **exceeds $250 — my earlier figure was wrong** |
| 240 | $242.67 | inside, no headroom |
| **200** | **$210.22** | **inside with headroom — recommended** |
| 150 | $169.67 | inside |

**200 contracts**, not 300. At clip 5 that is 40 fills rather than 60,
so the window or the concurrency has to carry more of the sample — which
is the honest trade, not a reason to raise the cap.

### Why clip 5, and why a price band at all

The fee is **banker-rounded to the cent per fill**, so the maker rebate
at a small clip is not merely small — it is **exactly zero**. The
minimum clip for a non-zero rebate is `C ≥ 0.4 / p(1−p)`:

| p | 0.50 | 0.30 | 0.20 | 0.10 | 0.05 | 0.02 |
|---|---|---|---|---|---|---|
| min contracts | 2 | 2 | 3 | 5 | 9 | 21 |

A clip of 5 inside `mid ∈ [0.20, 0.80]` clears it everywhere in the
band. Outside the band the required clip rises fast, and a bigger clip
is a bigger loss ceiling for no extra information — so the band is a
cost control, not a strategy choice. (If a market below 0.10 is ever
wanted, the clip must rise to 9, and the contract cap must fall to keep
$240.)

**What the fees actually are at clip 5** — and the asymmetry is the
whole economic problem, visible in cents:

| p | maker rebate | taker fee |
|---|---|---|
| 0.20 | +$0.0100 (1¢) | −$0.0556 (6¢) |
| 0.50 | +$0.0156 (2¢) | −$0.0869 (9¢) |
| 0.80 | +$0.0100 (1¢) | −$0.0556 (6¢) |

### Why the market set cannot be named yet, and the rule that names it

The 12 frozen incentive markets carry **Target Size 500**, which implies
books deep enough that a 5-contract order may sit behind hundreds and
**never be reached**. An experiment that returns P_FILL ≈ 0 because the
clip was invisible has measured nothing.

So the selection rule, fixed now:

> Take the **6 markets with the highest ratio of observed trade flow to
> median touch depth** over the observation window — the books where a
> small resting order is actually reachable. Require `mid ∈ [0.20,
> 0.80]` and `INSTRUMENT_STATE_OPEN` throughout.

That ratio is exactly what the 2026-09-23 observation run measures. The
two experiments connect: the observation picks the experiment's markets.

### CORRECTION 3 — zero fills is a result, not a failure

I wrote earlier that an experiment returning P_FILL ≈ 0 "has measured
nothing". That was wrong. **Zero fills at a recorded clip, price,
queue position and depth is a measurement**: it bounds P_FILL from above
for those conditions, and "a 5-contract order at the touch behind 500
resting contracts did not fill in six hours" is exactly the kind of fact
the strategy needs and does not have.

What matters is that the *conditions* are recorded precisely enough for
the zero to mean something — which is what the per-order record above is
for. The reachability rule is there to spend the budget where the answer
is most informative, **not** because a low fill rate would be a wasted
run.

### Why 300 contracts

Worst case is every filled contract naked and settling worthless:

| cap | worst case | |
|---|---|---|
| 200 | $160 | inside |
| **300** | **$240** | **inside — recommended** |
| 400 | $320 | exceeds $250 |

300 is the largest round cap that stays inside the existing $250 limit
at the top of the price band. It is conservative twice over: matched
pairs have **zero** exposure (a pair pays exactly 1.00), and not every
fill settles against us.

At clip 5 that is **60 fills**. If the fill rate is 15–30 %, that needs
roughly 200–400 placements — which the 6-hour window at 6 concurrent
two-sided quotes supplies.

### Committed collateral, separately

Per ECONOMIC_PACKAGE §8, a two-sided quote of size S encumbers
`(p_bid + 1 − p_offer)·S` — just under S dollars per contract-pair even
with **zero** fills. At clip 5 near mid 0.50 that is ≈ **$4.90 per
market**, so 6 concurrent quotes commit ≈ **$29.40** at any instant.
That is collateral, not loss, and it is separate from the $240 ceiling.

### The pre-trade check is the real control

`worst_case_remaining_loss ≤ $250` must hold **at the instant each order
is submitted**, with the pending-order race counted as though every
resting order fills. Enforced at the order path, not reconciled
afterwards. The 300-contract cap is a second, cruder backstop; both
apply, and the durable reservation (`bettor_live_control.reserve()`
before dispatch) is what makes the contract cap survive a crash or an
overlapping worker.

### A prerequisite stage, roughly $4

ECONOMIC_PACKAGE §8 lists four things that must be **read from a live
account** before any capital claim is made, and until they are, the
conservative assumption stands that resting orders fully encumber:

1. Does a resting order move `UserBalance.openOrders` and `buyingPower`?
2. Is `UserPosition.qtyAvailable` less than `netPosition` after a fill?
3. Does the venue net a matched pair, and **when** does the cash leave
   `unsettledFunds`?
4. Cancel→acknowledgement latency, and whether a fill lands inside it.

These need one order and a few reads. They should run **first**, as a
separate sub-stage, because every capital figure above assumes an answer
to (1).

---

## Why it must be real orders

A paper or shadow order does not answer the question. The three unknowns
above are all *about our order being in the book*: a simulated order has
no queue position, is never hit, and changes nothing. The experiment is
small **because** it is real, not instead of being real.

---

## What it would and would not settle

**Would:** `P_FILL` conditioned on decision-time observables, for the
market set and window tested. That unblocks `MAKE_YES`/`MAKE_NO` scoring
in the engine and turns the maker actions from `NOT_IDENTIFIED` into
priced ones.

**Would not:** profitability. A fill model makes the EV *computable*; it
does not make it positive. The two costs already measured — taker
completion at ~5× the maker credit, and adverse selection on the fills
that do happen — remain exactly as measured, and a fill model prices
them rather than removing them.

**Would not:** generalise beyond the tested set. A fill hazard measured
on 12 correlated culture markets is a fact about those markets.

---

## Approval this needs, stated separately

1. Funded orders enabled for a named market set, with a stated cash
   ceiling.
2. A trading-control change (`mirror_live`, `MAX_CONTRACTS`) — currently
   false and 0.0, and not changed by anything in the observation
   release.
3. The hard contract cap as a number.

**None of those is requested here.** This document exists so the request,
when it is made, is a single specified decision rather than another
round of design.
