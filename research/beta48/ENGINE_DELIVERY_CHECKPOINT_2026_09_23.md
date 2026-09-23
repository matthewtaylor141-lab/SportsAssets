# Investment engine — delivery gap, traced

**2026-09-23.** One Ferrari-inspired opportunity traced through the
**deployed** engine, input by input, with the exact code that consumes
each. Every number below is a measurement taken today against the
production database, not an estimate.

---

## 0. The short answer

The engine refuses every trade, and the three refusals have **three
different causes that must not be lumped together**:

| Action | Refusal | What it actually is |
|---|---|---|
| PAIR_BUY / PAIR_SELL | `COMPLEMENT_ABSENT` | **Unfinished engineering.** The one action needing no forecast. The join that would supply its input exists, is tested, and has zero callers. |
| TAKE_YES / TAKE_NO | fair value blocks | **A statistical result.** The model ran, on a protocol built to catch flattery, and lost to the venue price. No engineering changes this. |
| MAKE_YES / MAKE_NO | `P_FILL_NOT_IDENTIFIED` | **An external dependency.** Positive fill support requires an execution-tape join our data cannot supply. It needs our own orders. |

Only the first is hours of work. The headline — "valuation, execution
probability and independent EV are not established" — is true, but it
describes one unfinished wire, one completed experiment that came out
negative, and one measurement we are not in a position to take.

---

## 1. The trace

A Ferrari-inspired opportunity is a **pairing** trade: acquire both
legs, complete the pair, collect par. Its natural home in the engine is
`PAIR_BUY`, whose EV contains no forecast at all:

    EV = 1.00 − yes_ask − no_ask − fees

The pair pays exactly 1.00 at settlement whatever happens. No
settlement model, no fair value, no fill probability. This is the
action the engine is *most* ready to score.

Here is what happens to one, end to end.

| # | Step | Code | State |
|---|---|---|---|
| 1 | Market observed | collector → `bettor_state_observations` | implemented, connected, **running** |
| 2 | Row normalised | `bettor_observation_adapter.normalize` | implemented, **running** |
| 3 | Row → engine input | `bettor_observation_adapter.py:448` `to_book()` | implemented, **running**, and this is where it breaks |
| 4 | Decision | `workers/bettor_prospective.py:187`, `workers/bettor_live_loop.py:623` | implemented, **running** |
| 5 | Pair scored | `bettor_decision_engine.py:468–553` | implemented, **never reached** |

**Step 3 is the severed wire.** `to_book()` builds a `Book` from **one
record** and hardcodes the complement:

```python
    no_bid=None, no_ask=None, no_bid_size=0.0, no_ask_size=0.0,
```

So at `bettor_decision_engine.py:478` the first substantive test fails:

```python
    if book.complement_source != vc.OBSERVED:
        return Candidate(action=PAIR_BUY, status=NOT_IDENTIFIED,
                         blocker="COMPLEMENT_%s" % book.complement_source, ...)
```

The evaluation stops there. It never reaches the fee gate (`:500`), the
ask parse (`:509`), the depth check (`:517`) or the arithmetic (`:527`).
**No pair opportunity in production has ever been priced.** The
NO_TRADE that results is a missing-input placeholder, not an economic
judgment — which is exactly the distinction that has to end.

### The component that would fix it already exists and is orphaned

`bettor_observation_adapter.pair_legs()` (`:337`) joins two rows of the
**same contract** into a genuine complement pair. It is careful work:
it requires a declared complement family (not merely two different
labels), it requires source-clock alignment within a bound, it refuses
same-label repeats, and it returns every unpaired leg with a reason.

    pair_legs  non-test callers: 0

It is reachable from the decision path in every sense except that
nothing calls it. That is the unfinished engineering, and it is small.

---

## 2. Why it was left incomplete — and the measurement that changes the answer

I expected to report "wire the orphaned join, ~4h, done." The data says
that would be a fix nobody could use.

Measured today against production (`bettor_state_observations`,
2026-09-20 19:23Z → 2026-09-23 17:06Z):

```
 rows | yes_ask_num | no_ask_num | markets | legs
 4879 |        3339 |          0 |    4866 |  412

 outcome_leg    |  n   | priced
 no             | 3885 |   2661
 over           |  522 |    345
 yes            |    8 |      2

 joint_buckets | joint_markets
             2 |             2

 book_readability_status             |  n
 READABLE                            | 3380
 UNREADABLE:RateLimitError           | 1124
 UNREADABLE:NO_BOOK_LEVELS_PUBLISHED |  375
```

Four facts, each binding:

1. **4,879 rows over 4,866 distinct markets — 1.003 rows per market.**
   Each market is observed essentially **once, ever**. There is no time
   series for any contract. Nothing that needs two observations of one
   market — a reprice decision, a horizon, a markout — has any input.

2. **`no_ask` is numeric on zero rows.** The complement column has never
   been populated, because the sibling instrument is a **separate read
   the collector does not make**.

3. **`joint_buckets = 2.`** Across 4,866 markets and three days, exactly
   **two** ever had two distinct legs with a numeric ask inside the same
   minute. Wiring `pair_legs` into the decision path would therefore
   hand the engine ~2 candidate pairs per 3 days — *before* the
   complement-family, alignment and depth checks, any of which could
   take it to zero.

4. **1,124 of 4,879 reads (23%) failed with `RateLimitError`.** The
   collector is already rate-limited at its *current* one-leg-per-market
   rate. Reading the sibling leg doubles the request count.

So the binding constraint is not the orphaned join. **It is the shape of
what the collector requests:** one leg, one sample, per market — and it
is already being throttled. That is a request I have to put to you, not
an estimate I can work down.

---

## 3. Unfinished engineering vs. statistical validation vs. external dependency

**ENGINEERING (hours, mine to do).**
Wiring `pair_legs` into the decision path; a two-legged `to_pair_book`;
the strategy-specific valuation interface; persisted versioned model
artifacts and a feature schema; recording version, features, prediction
and uncertainty on every decision; unifying the shadow desk onto the
engine's decision implementation.

**STATISTICAL VALIDATION (not hours).**
Independent fair value is not missing integration — `bettor_fair_value`
says so in its own header. The models exist and ran, under a four-window
protocol with event-level disjointness:

    Delta log loss (blend − market)   −0.00926   the blend is WORSE
    95% event-clustered CI            [−0.00222, +0.02036]
    INCREMENTAL_SIGNAL_STATUS         NOT_DETECTED_AT_THIS_SAMPLE_SIZE

The venue price remains the strongest settlement forecast we have. Note
the status carefully: `NOT_DETECTED_AT_THIS_SAMPLE_SIZE` means the test
**could not resolve the question**, not that the answer is no. The model
is not ready and it is not refuted. No amount of engineering moves this
number; a better model or more events does.

**EXTERNAL DEPENDENCY (not schedulable by me).**
`bettor_p_fill` records the asymmetry that governs:

    TICKS_ALONE_SUPPORT_POSITIVE_FILL   False
    POSITIVE_FILL_SUPPORT_REQUIRES      EXECUTION_TAPE_JOIN

A tick row has no per-print tape, so volume-at-price, which side
consumed it, and our queue position are all unidentified — and every
fill model needs volume-at-price above zero. The four available
substitutes (whale completion, a touch, a price move, displayed depth)
are each forbidden by name and each for a good reason. Positive fill
evidence requires **our own resting orders**. That is circular with the
pilot, and I will not close the circle by substituting a number.

---

## 4. Estimate, in three separately-dated parts

I am deliberately **not** putting one date on all three.

### A. Integrated models producing shadow decisions

Engineering only. Everything below is work I can do against data and
code that already exist.

| Item | Hours |
|---|---|
| A1 `to_pair_book()` + batch records by market + wire `pair_legs` into both decision paths | 6 |
| A2 Strategy-specific valuation interface: completion probability within a horizon, expected completion price, partial completion, fees and rebates, capital commitment, residual-inventory outcomes, and the alternative set (wait / reprice / reduce / exit / hold) | 16 |
| A3 Persisted versioned artifacts, documented feature schema, and version + features + prediction + uncertainty recorded on every decision | 10 |
| A4 Unify the shadow desk onto the engine's decision implementation (it currently carries its own policy — `bettor_desk.py` does not import the engine) | 12 |
| A5 Acceptance examples — one admissible proposal, one refusal, both labelled — and correct presentation in the command centre | 6 |
| **Total** | **50** |

**Estimated complete: 2026-09-26.** Assumptions, stated so you can hold
me to them: uninterrupted work on this and nothing else; no further
defect found in the shadow accounting (item 5 below); A2's alternative
set is valued only where each term is identified, refusing the rest by
name rather than growing to cover them.

**What A does *not* give you.** With the feed as it stands, an
integrated pair path evaluates ~2 candidates per 3 days. A is the
machinery working and provably evaluating real opportunities. It is not
a strategy producing a rate of decisions.

### B. Models qualified by prospective evidence

**Not datable from engineering.** B needs a sample, and a sample needs
an evaluable-opportunity rate we do not currently have. The critical
path to B runs through the **collector's request shape**, not through
my code:

    sibling-leg read + repeat sampling
      → evaluable pair rate
        → sample
          → prospective qualification

I will not name a date for B until the feed decision in §6 is made,
because any date I gave would be a function of a number nobody has
chosen yet.

### C. Readiness for a funded pilot

**Not datable at all** on present evidence, and the reason is structural
rather than schedule-related: C requires `P_FILL`, `P_FILL` requires an
execution-tape join, and the only source of that join is our own orders.
A bounded pilot is what would resolve it. Its request is in §6.

---

## 5. Critical path

    collector request shape  ──►  evaluable pair rate  ──►  sample  ──►  B
              │
              └── (independent) ──►  A1…A5 engineering  ──►  A

    our own resting orders  ──►  P_FILL positive support  ──►  C

**A is not on the critical path to B or C.** That is the single most
important line in this document. I can finish every hour of A and be no
closer to a qualified model, because what B is short of is opportunities
and what C is short of is executions. Doing A remains correct — it is
what makes the refusals honest, turns missing-input placeholders into
priced economic answers, and is a precondition for both — but finishing
it will not produce a qualified strategy, and I will not report it as
though it had.

---

## 6. The two requests, prepared rather than assumed

Neither is taken. Both are stated precisely so each is one decision.

**REQUEST 1 — sibling-leg and repeat sampling (resolves A→B).**
Read the complement leg of a market in the same cycle as the leg already
read, and sample admitted markets more than once. What it resolves: the
evaluable pair rate, which is currently 2 per 3 days and unknown at any
other sampling regime. What it costs: up to 2× the current per-market
request count, against a collector already returning `RateLimitError` on
**23%** of reads. **This is not free and I am not treating it as
authorized.** Until it is decided I will build A against the data as it
is and report the rate honestly.

**REQUEST 2 — bounded fill-measurement pilot (resolves C).**
Exactly what it would resolve: `P_FILL` for our own resting orders at
defined prices, sizes and horizons — including partial fills,
cancellation, and adverse price movement after fills — which no
observational dataset can supply. Scope, limits and instrumentation to
be written once A3 exists, so the pilot records against a real feature
schema rather than an improvised one. Not requested today.

---

## 7. Shadow accounting (directive item 5) — current state

Resolved before resuming the affected desk, as required.

- **Contained.** `acct_fc2d773a2afa4851` is `paused=t`,
  `ACCOUNTING_UNCERTAIN`, `performance_qualification=SUPPRESSED`, since
  16:51:38Z. The flag is DB-backed and re-read every cycle.
- **The old writer stopped.** Last desk write of any kind 16:37:30Z. The
  advisory lock is held by a backend that started **16:51:40Z** — after
  the flag landed — so the incumbent's connection is gone. No session
  was terminated to achieve this.
- **Not verified, and labelled as such:** `last_verified_at` is null and
  the page renders `NOT YET VERIFIED ON THE CORRECTED BUILD`. The
  corrected id scheme cannot be confirmed in production while the desk
  is paused — all 63 orders and 583 decisions still carry
  `LEGACY_COUNTER` ids because the corrected build has written nothing.
  That is the correct state, and it is a gap, not a pass.
- **The damaged history is preserved and excluded** from performance
  qualification and from training. Measured drift $2,367.72, incident
  `DESK_ACCOUNTING_RECOVERY_2026_09_23`, P&L
  `UNRELIABLE_DO_NOT_QUOTE`.

Two defects were found and fixed while writing this, both by reading
production output rather than by reasoning about the code: a paused desk
was still opening an epoch row on boot (the pause is now read before any
startup write), and `positions_qty_ne_fills` was a check that compared
nothing and printed the position count under a discrepancy count's label.

---

## 8. What I am doing next, without stopping for approval

A1 then A2, in that order, against the data as it stands. The first
deliverable is a real observation reaching `PAIR_BUY`'s arithmetic and
coming out with a **number** — almost certainly a negative one, since
`1.00 − yes_ask − no_ask − fees` is negative whenever the two asks
straddle par by more than the fee — and that negative number, recorded
with its inputs, is worth more than 11,923 placeholders. It is the
difference between "we could not evaluate this" and "we evaluated this
and it loses 3.1 cents a contract."
