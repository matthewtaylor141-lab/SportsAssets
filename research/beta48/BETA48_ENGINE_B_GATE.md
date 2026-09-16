# BETA48 — Engine B gate

Preregistration: `ENGINE_B_PREREGISTRATION.md`, committed at `1af02f8`
**before** any result was observed. Nothing below moved a threshold, a
split, a metric or a gate after the fact.

---

## THE GATE

```
BETA48_ENGINE_B_GATE       = FAIL
BEST_NATIVE_CANDIDATE      = NONE
ECONOMIC_MECHANISM         = n/a — no candidate cleared its gate
FINAL_HOLDOUT_NET_ROI      = +1.47 pp  (M2, 100 bp fee, clustered by condition)
                             -2.15 pp  (same trades, equal-weighted per trade)
FINAL_HOLDOUT_NET_PNL      = -$76.83 per $1 staked per trade over 3,574 trades
INDEPENDENT_HOLDOUT_EVENTS = 720 conditions
OPPORTUNITIES_PER_DAY      = 324.9 over 11 holdout days
ESTIMATED_CAPACITY         = $771 median top-of-book notional per trade;
                             $7,019,077 aggregate. PARTIAL — top-of-book
                             only, on an RN1-selected population.
EXECUTION_MODEL            = BUY only, TAKER only, at the observed best ask,
                             capped at observed top-of-book. No maker fills.
EXECUTION_EVIDENCE         = NONE for BETTOR. This is Polymarket CLOB;
                             BETTOR trades PMUS. BETTOR_PASSIVE_FILL_
                             PROBABILITY = NOT_IDENTIFIED; BLOCK_4 stands.
CURRENT_REGIME_EVIDENCE    = NONE. Data ends 2026-09-11; today is 2026-09-16.
BIGGEST_REMAINING_UNKNOWN  = whether ANY tradable edge survives the
                             executable spread on a venue we can actually
                             trade — the spread is now measured (1.0 c
                             round trip) and no measured signal exceeds it.
READY_FOR_FORWARD_SHADOW   = NO
READY_FOR_MICRO_LIVE       = NO
SHORTEST_NEXT_EXPERIMENT   = see "If anything is worth doing next" below.
```

---

## What was tested, and what killed it

### M1 — favourite–longshot bias at the reconstructed mid: **KILLED AT TRAIN**

Killed on two independent grounds, both preregistered:

1. **The declared kill condition fired.** TRAIN favourite deficit
   `settle − mid` = **+0.0023**, inside the ±1 cent band the
   preregistration names as "the bias is the spread, not a mispricing".
   95% CI `[−0.0379, +0.0425]`, clustered over 350 conditions.
2. **Arithmetic.** On a binary reconstructed from two asks,
   `mid₀ − ask₀ = −spread/2` **identically** (verified: max deviation
   1.11e−16 over 7,149 rows). Taking the ask always pays exactly half
   the round-trip spread, so a fair value that departs from the mid by
   less than the half-spread can never pay. TRAIN: bias +0.0023 against
   a half-spread of 0.0100 → **net −0.0077**. It does not clear.

The holdout was **not** read for M1.

### M2 — touch quality: **MECHANISM CONFIRMED, ECONOMICS FAILED**

The microstructure claim is real and replicated on TRAIN: when the touch
is thin, the mid is measurably worse calibrated.

| cohort | conditions | mean \|settle − mid\| |
|---|---|---|
| THIN (< 39.8 shares, TRAIN 25th pct) | 125 | 0.3947 |
| DEEP (≥ 39.8 shares) | 285 | 0.3455 |

Difference **+0.0492**, SE 0.0237, **z = +2.07**, 95% CI
`[+0.0027, +0.0957]` — excludes zero. Thin touches genuinely are worse
prices. M2's own train kill did not fire, and M2 beat M1 on validation
(+3.58 pp vs +3.30 pp at 100 bp), so it earned the single holdout read.

**Final holdout, read once, rule frozen from TRAIN:**

| fee | trades | conditions | net ROI | 95% CI |
|---|---|---|---|---|
| 0 bp | 3,574 | 720 | +2.47 pp | [−1.88, +6.83] |
| **100 bp** | 3,574 | 720 | **+1.47 pp** | **[−2.88, +5.83]** |
| 200 bp | 3,574 | 720 | +0.47 pp | [−3.88, +4.83] |

Against the preregistered gate:

| criterion | required | actual | |
|---|---|---|---|
| net ROI at ≥100 bp | > +3.30 pp | +1.47 pp | **FAIL** |
| clustered CI lower bound | > 0 | −2.88 pp | **FAIL** |
| independent conditions | ≥ 200 | 720 | PASS |

**Two of three fail. M2 is killed.**

### Three further facts that make the kill unambiguous

1. **The two weightings disagree in sign.** The preregistered metric
   weights each condition equally (+1.47 pp). Weighting each trade
   equally — which is what actually happens to money — gives
   **−2.15 pp**, and the raw dollar sum is **−$76.83**. A point estimate
   whose sign depends on the weighting is not an edge. The asymmetry is
   visible in the structure: 499 winning conditions with a median of 1
   trade each, 221 losing conditions with a median of 2.
2. **It does not survive a half-cent of worse execution.** At ask +0.005
   ROI falls to +1.08 pp; at ask +0.010 it is **−1.32 pp**. The measured
   round-trip spread is 1.0 cent, so this is well inside normal
   execution variation. (The ask +0.020 row shows +19 pp on **10 trades
   / 8 conditions** — that is noise, and it is printed rather than
   quietly dropped precisely because it is the kind of cell a
   threshold-miner would seize on.)
3. **Its whole apparent edge is smaller than the fee uncertainty.**
   `FEES_VERIFIED = NOT_IDENTIFIED` for CLOB. The gap between 0 bp and
   200 bp is 2.0 pp; the point estimate is 1.47 pp. The candidate cannot
   be distinguished from its own unverified cost assumption.

---

## What is now established and should be preserved

These are the sprint's durable results, independent of the FAIL.

```
DUTCH_ARBITRAGE_ON_CLOB       = NOT_PRESENT
EXECUTABLE_ROUND_TRIP_SPREAD  = 1.0 cent median / 1.95 cents mean (CLOB)
MID_IS_RECONSTRUCTIBLE        = YES, bid(leg0) = 1 - ask(leg1)
THIN_TOUCH_IS_WORSE_PRICED    = OBSERVED (+0.049 abs mid error, z=+2.07)
FAVOURITE_LONGSHOT_AT_MID     = NOT_IDENTIFIED (+0.0023 on train, CI spans 0)
TRACK_P_PREMISE               = EXPLAINED — the "expensive ask" is the spread
```

**Track P's premise is now explained rather than merely failed.** Track
P found `SETTLE − ASK` negative in 9 of 10 price bands and read the ask
as systematically expensive. It is — on **both** legs, by the round-trip
spread. `ask(leg0)+ask(leg1)` has median 1.0100 and **minimum 1.0000**
across 4,101 near-simultaneous pairs, with **zero** sub-parity
observations. A deficit that appears symmetrically on both sides of a
binary carries no direction and cannot be traded. That is why Track P
failed, and it is a stronger result than the failure alone.

It also closes mechanism class B by measurement: there is no dutch
arbitrage on this venue in this window. The apparent sub-parity that
appears at wider time gaps (41% below parity at a 1-hour gap) is
**staleness**, not opportunity — it grows monotonically with the gap and
is exactly zero at simultaneity.

---

## Disclosure

While running the TRAIN directional test I printed the same statistic
for all three splits in one command, so the holdout favourite deficit
(+0.0193) was visible before the holdout was formally read. It did not
inform any choice: M1's kill fired on TRAIN and was preregistered, and
every parameter of the M2 rule — the bias `B = 0.0196`, the touch cut
`39.8`, the threshold `0` — comes from TRAIN only. But the holdout is no
longer pristine for that one statistic, and that is worth more said than
unsaid.

---

## Why no third mechanism was tested

The preregistration selected two, not three, and named why the other
three classes were unavailable rather than padding the list:

- **A** (dislocation vs independent context) — we hold no score state,
  no live state, no odds feed, no second book. Nothing to disagree with.
- **B** (known-payoff relative value) — **closed by measurement above**.
- **E** (cross-venue) — Phase X frozen at `b5b2b63`, both hosts
  `connect_rejected`; Phase X1 recorded a side-orientation
  **contradiction**, so equivalence is contradicted, not merely
  unverified.

---

## If anything is worth doing next — and the honest answer about cost

`SHORTEST_NEXT_EXPERIMENT` is the **DATA-B forward capture**, unchanged
from `TRACK_P_DATA_ARCHAEOLOGY.md`: enumerate the whole PMUS sports
board, snapshot every eligible market two-sided at T−60 min and
T−10 min, and follow each to settlement. It is the only experiment that
would supply what every dead candidate lacked — a two-sided price, on
**BETTOR's own venue**, with an **independent selection rule**, and an
outcome.

Three things must be said plainly about it:

1. **It cannot run inside this sprint.** It needs ~2,500 settled markets
   and `PMUS_SETTLEMENT_THROUGHPUT = NOT_IDENTIFIED`, so its duration
   cannot even be stated in advance — the capture measures it in its
   first days. That is days-to-weeks, not hours.
2. **It needs egress this container does not have.** See
   `PHASEX_EGRESS_REQUEST.md`. It is read-only and needs no credential,
   no order and no production change, but it does need a host that can
   reach `gateway.polymarket.us`.
3. **It resolves whether predictive information EXISTS, not whether we
   can execute on it.** `BETTOR_PASSIVE_FILL_PROBABILITY` stays
   `NOT_IDENTIFIED` either way, and BLOCK_4 stands: 22,297 displayed
   shares against 180 traded in 16 minutes, zero touches.

I am **not** starting it. It is outside the sprint, it needs egress I
was told not to re-dispatch Phase X for, and the correct output of this
sprint is the NO-GO below rather than another collection programme.

---

## NO-GO

We do **not** currently possess a strategy with sufficiently demonstrated
positive expected value to justify even a tiny controlled live
validation.

Engine A closed on total economics. Engine B has no candidate: one died
at train on arithmetic, one died at the holdout on its own frozen gate.
Engine C was never reachable — every inventory-level path to it requires
assuming an unpaired leg can be exited cheaply, and
`UNPAIRED_LEG_EXIT_COST = NOT_IDENTIFIED` with a sell channel of
0.00%–0.54% of lot stake across six reference accounts, so there is no
evidence base to price one from.

`mirror_live = false`. No orders, no capital, no production activation,
no Track A change, no Phase X re-dispatch.
