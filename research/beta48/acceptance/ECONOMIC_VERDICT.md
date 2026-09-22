# BETTOR — the economic verdict on our own engine

**Does our evidence support a profitable BETTOR policy today?**

> **No policy is SUPPORTED FOR A BOUNDED PILOT on today's evidence.**
> Two are **REJECTED** outright. Two are **UNRESOLVED** with the
> missing quantity named, its break-even calculated, and the smallest
> experiment that resolves it specified. One is **NOT_COMPARABLE**.
>
> The surviving candidates fail on a single, identified quantity —
> not on a wall of unknowns — and that quantity is measurable only by
> resting our own orders.

---

## 1. Reproducible commands

```bash
# the decision engine's own accounting, and its 25 unit tests
python -m pytest research/beta48/test_bettor_policy_ev.py -q

# candidate-by-candidate economics, through the engine
python research/beta48/bettor_economic_test.py

# what would have to be true for the maker classes to earn
python research/beta48/bettor_maker_breakeven.py
```

All three import `research/beta48/bettor_policy_ev.py`. **There is no
spreadsheet.** Every price, fee, rebate and refusal in this document
comes from `incremental_ev()` — the same function the engine's
policies call.

## 2. Datasets, coverage, and the development/holdout line

| dataset | identity | size | status |
|---|---|---|---|
| PMUS BBO capture | `research/evidence/capture/run85_phase2_segment_*/request_log.jsonl.gz` | 27 segments, 30,590 verbatim HTTP 200 bodies | **DEVELOPMENT** |
| PMUS curated pairs | `research/beta48/acceptance/bbo_book_real_400.json` | 400 pairs | **DEVELOPMENT** |
| RN1 fills | `research/snapshots/u2_events_v1.jsonl.gz` | 214,609 fills, 17,752 conditions, 37 days | **DEVELOPMENT**, global CLOB |
| Track P holdout | `research/evidence/phasex*` | 1,170 independent markets | **SPENT** — locked negative |

**NO UNTOUCHED PMUS HOLDOUT REMAINS.** The Class C test read *all 27*
capture segments. They were a genuine holdout for that test, opened
once, and they are development data now. Nothing in this document is
a holdout result and none is claimed. I am not relabelling them.

Two-sided open observations across the whole capture: **1,551
observations on 11 distinct markets.** Eleven. That is the coverage
ceiling on anything derived from this corpus, and it is why the
market — not the observation — is the unit everywhere below.

**The only remaining paths to a genuine out-of-sample test are
prospective**: the observation probe (for state-conditioned movement)
and a bounded maker pilot (for fill-conditioned return).

## 3. Frozen specification

Frozen before the numbers below were computed:

- **Policies**: the engine's `POLICIES` dict — `taker_pair`,
  `passive_maker` — plus the classes enumerated in
  `STRATEGY_EVIDENCE_MAP.md`.
- **Primary metric**: expected cash per contract, net of the
  **verified** PMUS fee schedule (`Θ_taker +0.06`, `Θ_maker −0.0125`,
  banker's rounding per fill, effective 2026-07-01).
- **Benchmark**: `DO_NOTHING`, which the engine prices at exactly 0.
- **Acceptance**: a policy is supported only if expected cash is
  positive **and** its confidence interval excludes zero under
  defensible execution assumptions.
- **Unit of analysis**: the market, not the observation.

## 4. Candidate-by-candidate

### Class C — taker complementary pair · **REJECTED**

`ask + (1 − bid) = 1 + spread` is an **identity**: the pair costs par
plus the spread before either taker fee. Measured on the capture:
median basis **1.0050**, minimum **1.0050**, pairs below par **0**,
profitable after fees **0**, median EV **−0.0325/contract**.

Not retested here — the user is right that algebra already settles it.

### Class D — Phase X cross-venue PMUS/Kalshi · **REJECTED, LOCKED**

ROI +10.5% TRAIN → +0.5% VALIDATION → **−11.1% HOLDOUT**, 95% CI
[−15.5%, −6.6%], 1,170 independent markets, **negative at zero fees**.
No fee correction revives it. Preserved as a negative result; not to
be retuned.

### "Copy the profitable account" · **REJECTED**

The pair channel — the only mechanism a copy strategy reproduces —
**changes sign across the six accounts**: three positive, three
negative. Between 43.9% and 91.9% of the capital behind every headline
is *held to settlement*, not traded as pairs. And RN1, the one account
whose raw fills we hold, does not demonstrate its own edge: edge_roi
+2.084%, 95% CI **[−0.195%, +4.362%]** — crosses zero — decaying
+2.08% (37d) → **+0.26% (7d)**.

Another account's profit is not our executable edge, and this one is
not reliably a profit.

### Classes A and B — maker · **UNRESOLVED**

**The missing quantity**: `E[SETTLEMENT − QUOTE | FILLED, STATE]` —
what a resting order earns against the flow that actually fills it.

**Break-even, computed exactly** (`bettor_maker_breakeven.py`):

```
value_if_filled = as_fill + rebate        EV = p_fill × value_if_filled
```

`p_fill` **multiplies**. It cannot change the sign — it sets the rate,
the turnover and the capacity. So the sign question reduces to
`as_fill` versus the rebate, and that is arithmetic on a verified fee
schedule:

| | |
|---|---|
| measured median half-spread (PMUS) | **0.0025** |
| maker rebate at median mid 0.13, 100-contract fill | **0.0014/contract** |
| **break-even `as_fill`** | **−0.0014/share** |
| toxic stress case (global CLOB, RN1-selected) | **−0.0090/share** |
| **break-even toxic fraction** | **33.9%** |

Below 33.9% RN1-grade toxic flow the policy earns; above it, loses.
`TOXIC_FILL_FRACTION` on PMUS is **NOT_IDENTIFIED**.

**A fee finding that changes the arithmetic.** The venue computes
`Θ·contracts·p·(1−p)` and rounds the **result** to the cent,
half-to-even, **per fill**. At mid 0.13 the raw rebate on one contract
is 0.001414 — which **rounds to zero**:

```
   1 contract    0.000000/contract     (rounds away)
   2 contracts   0.000000/contract     (rounds away)
   4 contracts   0.002500/contract     (rounds UP)
 100 contracts   0.001400/contract
1000 contracts   0.001410/contract
```

**A maker filling in ones and twos earns no rebate at all.** Any
per-share rebate quoted without a fill size is a number the venue will
not pay.

### Class E — directional taker · **NOT_COMPARABLE**

`FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED`. The engine refuses to price
`CROSS_BUY` without a caller-supplied fair value, and correctly so.
There is no edge estimate to rank.

## 5. Execution sensitivity — and it is fragile

| scenario | EV/fill | |
|---|---|---|
| 0% toxic | +0.003900 | EARNS |
| 10% toxic | +0.002750 | EARNS |
| 25% toxic | +0.001025 | EARNS |
| 33.9% toxic | 0.000000 | break-even |
| 50% toxic | −0.001850 | LOSES |
| 100% toxic | −0.007600 | LOSES |

**Now take away half the assumed half-spread edge** — which quoting at
the touch does, and **493 of 788** observations in this corpus sit on a
half-cent grid that leaves no room to quote inside:

| scenario | EV/fill | |
|---|---|---|
| 0% toxic | +0.002650 | EARNS |
| 25% toxic | **+0.000088** | ~zero |
| 33.9% toxic | **−0.000826** | **LOSES** |

**Profitability disappears under modestly worse execution.** That is a
material result and it is the honest headline: the edge, if it exists,
is roughly one tick wide and a grid that denies us the inside quote
consumes most of it.

**The assumption the whole scenario rests on** is that an uninformed
fill leaves us the half-spread — true only if the mid is our fair
value. `FV_BETTOR_INDEPENDENT` is NOT_IDENTIFIED. A pilot tests this
first.

## 6. Capacity and capital, against the mandate

| | |
|---|---|
| **$500,000/day** | **NOT SUPPORTED by measured opportunity** |
| binding constraints | traded volume and queue position, both measured |
| at optimistic volume, 1,000-share display | needs **91%** of every addressable market quoted continuously |
| at the wider 6-market sample's volume | needs **many times the whole census** |
| observed queue | **22,297 shares ahead**, **33 hours** to the front, **zero touches in 16 minutes** the one time it was watched |

Working capital is **NOT_IDENTIFIED**: inventory holding time (never
observed — no BETTOR fill has occurred), outstanding-order collateral
(undocumented; could dominate for a wide quoter), and release
mechanics (same-day vs next-day changes the requirement by a
settlement cycle). The **$41,667** figure is a scenario under a
two-hour holding assumption and a stated turnover convention — a bound
only within those conditions, and unconditionally neither a bound nor
an estimate.

**Supported scale, stated separately from the target: zero.** No
policy is supported for capital today.

## 7. What would resolve it — the smallest experiment

**Measure `as_fill` on BETTOR's own admitted fills.** That is the
quantity by definition, and nothing else identifies it. A perfect
prospective capture of an unselected PMUS universe identifies
`E[SETTLEMENT − QUOTE | STATE]` and leaves **fill**-selection bias
exactly where it was.

Pilot shape, **prepared, not requested** (preparation needs no capital
permission; activation is separately authorized):

| | |
|---|---|
| objective | estimate `as_fill` and `TOXIC_FILL_FRACTION` with an interval that excludes 33.9% |
| loss cap | the pilot's entire deployed capital, hard-capped at the order path |
| stopping rule | stop on cap, on deadline, or when the interval excludes break-even in either direction |
| scaling criterion | scale only if the lower bound of `as_fill` exceeds −0.0014/share at the fill size actually achieved |
| measurement | realized (settlement − our fill price), matched per fill, clustered by market |

## 8. Delivery estimate

| work | elapsed |
|---|---|
| everything in this document | **complete** |
| observation probe (state frame, transport) | 30 min per probe; **the run is bounded at 1,800 s** |
| `E[SETTLEMENT − QUOTE \| STATE]` at usable coverage | **market-observation time, not work time** — needs enough distinct markets observed to settlement; the 11-market ceiling here is the constraint, not compute |
| `as_fill` | **requires an order path and capital** — not authorized, and not requested by this document |

The first two columns are mine to finish. The third is elapsed market
time and cannot be compressed by working harder. The fourth is a
decision, not a task.

---

## The direct answer

**Does our evidence support a profitable BETTOR policy today?**
**No.** Two candidates are rejected, two are unresolved on one named
quantity, one cannot be priced at all.

**At what scale?** None. Supported scale is **zero** today, against a
$500,000/day target that measured opportunity does not support by a
wide margin.

**Under which assumptions could the maker classes earn?** Fewer than
**33.9%** of fills RN1-grade toxic, at **100-contract** fill sizes (at
1–2 contracts the rebate rounds to zero and break-even rises to
`as_fill ≥ 0`), **and** the mid being our fair value. Lose half the
half-spread — which the observed half-cent grid does — and the
break-even toxic fraction falls to roughly **25%**, at which point the
EV is indistinguishable from zero.

That is a narrow, fragile, and **testable** window. It is not an edge
we have demonstrated.
