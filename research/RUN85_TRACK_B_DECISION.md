# RUN 85 TRACK B — 48-HOUR PROFITABILITY DECISION TRACK

**Exploratory. Separate from Track A. Nothing here may modify or feed back into
the frozen seven-day capture.**

| | Track A | Track B |
|---|---|---|
| purpose | scientific capture | profitability decision |
| status | FROZEN — architecture closed | exploratory, this document |
| writes | `research/evidence/capture/**` | `research/trackb/**` only |
| reads | the venue | Track A's committed segments (read-only) |
| may change Track A? | — | **NO**, under any result |

Track B reads Track A's sealed bytes the way any outside auditor would. It never
edits them, never re-runs a segment, never re-selects a market, and never tunes
Track A on what it finds. Proof runs #2 and #3 are **instrumentation validation
only** and are excluded from every primary economic statistic here; where a proof
segment is used at all it is to prove an analyzer, and it is labelled as such.

Inter-segment gaps are real missing intervals. Nothing in Track B interpolates
across them, and no statistic is computed as if the gap were observed.

---

## FINDING B-1 — THERE IS NO INDEPENDENT COMPLEMENTARY BOOK

Measured across all 110 populated `/bbo` responses in the run #3 proof segment,
**zero exceptions**:

```
longQuote  == bestAsk              110 / 110
shortQuote == 1 - bestBid          110 / 110
```

therefore, identically:

```
longQuote + shortQuote  ==  1 + (bestAsk - bestBid)  ==  1 + displayed spread
```

`shortQuote` is not a second order book. It is a re-presentation of the **same**
book's bid. This settles the pair question structurally rather than empirically:

| pair construction | cost | gross pair edge |
|---|---|---|
| **taker / taker** — buy long at ask, buy short at shortQuote | `1 + spread` | **−spread** |
| **maker / maker** — rest a bid at bestBid, rest an ask at bestAsk, both filled | `1 − spread` | **+spread** |

So crossing both sides is a **guaranteed loss of exactly the displayed spread**,
every time, with no uncertainty at all. The only construction that can earn the
spread is passive on both legs.

**What this does to the primary question.** The whole of it now reduces to one
thing: *does the maker/maker pair actually complete, and what is consumed between
the two fills?* The displayed spread is the theoretical **maximum** gross edge —
the ceiling, not the result. Adverse selection, the unmatched-leg residual, fees
and realistic size are all subtracted from it, and the strategy is profitable only
if what survives is positive.

This is the sharpest possible form of *displayed spread ≠ profit*: the spread is
now provably the entire budget, and every real cost is drawn against it.

### What B-1 does NOT establish

- **Executability.** The arithmetic relationship is proven; that an order resting
  at `shortQuote` would execute is **NOT_IDENTIFIED**. B-1 is about prices the
  venue displays, not about fills.
- **Durability.** One 900-second segment, 11 markets. The identity is exact and
  algebraic in shape, but it is re-verified on every Track A segment; a single
  counterexample retracts it.
- **Anything about profit.** B-1 bounds the numerator. It says nothing yet about
  whether the numerator survives contact with reality.

---

## THE REGISTERS

Every hypothetical passive opportunity is classified, and the classes are kept
apart. The primary dataset is `ALL_POSTABLE` and is conditioned on **nothing** —
not on the book moving, not on a touch, not on the sign of the outcome.

```
POSTABLE      a two-sided book existed at t0, so a passive quote could be placed
TOUCHED       best_ask(t+h) <= p_bid   (buy)  /  best_bid(t+h) >= p_ask  (sell)
CROSSED       strict inequality of the same
NOT_TOUCHED   postable, and neither
```

Forward markouts at 5 / 10 / 30 / 60 s. **An unchanged valid book is a zero
markout and it is retained.** Reported separately for ALL_POSTABLE, TOUCHED and
CROSSED — never pooled, never conditioned.

---

## FILL MODELS

| | rule | queue treatment |
|---|---|---|
| **F0** pessimistic | crossed **and** the resting quantity at our price demonstrably consumed | we are behind the entire pre-existing queue |
| **F1** conservative | crossed (strict) | behind the pre-existing queue; only volume beyond it fills us |
| **F2** moderate | touched (at or through) | behind the queue, partial credit |
| **F3** upper bound | touched = filled | none |

**F3 is an upper bound and is never the primary profitability result.** It exists
to show what the answer would be under an assumption known to be false, so that a
result which is negative even under F3 can be called negative without argument.

Per model: opportunities · estimated fills · fill rate · average entry price ·
adverse markout at 5/10/30/60 s · complementary-side completion rate · time to
completion · pair cost · incomplete-pair exposure · pre-fee expectancy.

---

## THE WATERFALL

```
DISPLAYED_GROSS_EDGE            (= spread, the ceiling B-1 establishes)
  - ADVERSE_SELECTION
  - INCOMPLETE_PAIR_COST
  = PRE_FEE_EXPECTANCY
  - VERIFIED_FEES
  + VERIFIED_REBATES
  = EXPECTED_NET_EDGE
```

Fees are **unresolved**, so the last two lines are not computed. Track B reports
`PRE_FEE_EXPECTANCY` and `BREAK_EVEN_TOTAL_FEE` — the total round-trip fee at
which the strategy reaches zero — and **no `NET_EXPECTANCY` is manufactured.**

`PMUS_FEES_RESOLVED = NO` stands. The `feeCoefficient = 0.06` carried on discovery
rows is a **field, not a rule**; nothing is inferred from it alone.

---

## CAPACITY

Estimated per day: opportunities · fills · median and p25 executable size ·
capital tied up · median completion time · expected dollars · turnover.

**Executable size is near-touch depth, never the full displayed ladder.** Whole-
ladder depth was retracted as a queue proxy in Phase 2D and does not return here.

---

## CHECKPOINTS

Anchored to the first unattended Track A segment, `2026-09-13T18:00:00Z`.

| checkpoint | UTC |
|---|---|
| +6 h | 2026-09-14T00:00:00Z |
| +12 h | 2026-09-14T06:00:00Z |
| +24 h | 2026-09-14T18:00:00Z |
| +48 h | 2026-09-15T18:00:00Z |

Classification at each: **A** strong candidate · **B** conditional · **C**
displayed edge only · **D** negative · **E** not identified.

**Track A is not tuned on any checkpoint result.** A bad checkpoint is a finding,
not an instruction to change the instrument.

---

## FEE REGISTER — as of checkpoint +6h (2026-09-14T00:00Z)

`PMUS_FEES_RESOLVED = NO.` The documentation authorization was granted and is
not the constraint: **every Polymarket-owned host is refused by the organization
egress policy**, which is a separate control from the trading-capability
boundary.

```
docs.polymarket.us      EGRESS_BLOCKED
polymarket.us           EGRESS_BLOCKED
help.polymarket.com     EGRESS_BLOCKED
```

The proxy README is explicit that a policy denial must be reported, not routed
around, so no third-party mirror is treated as a substitute for the primary text.

### What the authorized venue host does publish

Mined from already-sealed `/v1/events` payloads — **zero new venue contact**:

| | |
|---|---|
| `feeCoefficient` | `0.06` on **51,566 / 51,566** market rows — one distinct value |
| rewards / incentive / rebate / maker / taker field | **absent from every market row** |
| `metadata` | sports identity only (player, team, line, stat) — nothing economic |

So the public gateway exposes exactly one fee-shaped number and **nothing
whatever about liquidity rewards**. There is therefore no authorized way to tell
whether a frozen-cohort market participates in a rewards program.

### The three components, kept separate

| component | status | evidence |
|---|---|---|
| `PMUS_TAKER_FEE_RULE` | **NOT_RESOLVED** | one primary datum, `feeCoefficient = 0.06`, uniform. A coefficient is not a formula. |
| `PMUS_MAKER_FEE_RULE` | **NOT_RESOLVED** | no primary evidence that a maker pays anything, or does not |
| `PMUS_MAKER_REBATE_RULE` | **NOT_RESOLVED** | no primary evidence |
| `PMUS_LIQUIDITY_REWARD_RULE` | **NOT_RESOLVED** | program not visible on the authorized host |
| cohort reward eligibility | **NOT_IDENTIFIED** | no per-market reward field exists to read |

Secondary web summaries were returned by search and are recorded as
**UNVERIFIED, LOW confidence, NOT USED in any number here** — they also
contradict each other (a `0.05` taker coefficient with a `-0.0125` maker rebate
in one, `0.06 × C × p × (1−p)` in another, a flat `0.20%` maker rebate in a
third). Conflicting secondary sources are not a resolution, and the coincidence
between one of them and the venue's own `0.06` is *not* permission to adopt the
rest of that sentence.

### THE DECISIVE SENSITIVITY — why this blocker outranks the others

Take the unverified taker form `0.06 · p · (1−p)` purely as a **sensitivity**, not
as a fee rule. At `p = 0.50` it is `0.06 × 0.25 = 0.015` per contract per side.

The frozen cohort's tick sizes are 0.005 and 0.01, and most observed spreads are
one tick. So the whole maker/maker gross pair edge is **0.005–0.01**, while a fee
of that shape would be **0.015**.

> If the maker pays anything resembling the taker formula, the fee alone is
> **one and a half to three times the entire gross edge**, and the strategy is
> negative before adverse selection is even considered. If the maker pays zero
> and collects a rebate or reward, the same strategy can be positive.

**The sign of the answer is decided by an unresolved fact.** No amount of Track A
capture changes that: more markout data refines the subtraction, it cannot tell
us the fee. This is the highest-leverage open item in the 48-hour sprint, and it
is blocked on egress policy rather than on measurement.

`BREAK_EVEN_TOTAL_FEE` is bounded above by the displayed spread and cannot exceed
it; the exact value needs `PRE_FEE_EXPECTANCY`, which needs primary segment data.

---

## OPEN BLOCKERS

1. **Fees — blocked on authorization, not on effort.** The published fee schedule
   is not on `gateway.polymarket.us`, which is the only host this work is
   authorized to read. Resolving maker fee, taker fee, rebate, formula, rounding,
   currency and settlement treatment needs either (a) permission to fetch
   Polymarket US public documentation pages on another host, or (b) an
   authenticated fee endpoint, which is forbidden. Until then: `PRE_FEE_EXPECTANCY`
   and `BREAK_EVEN_TOTAL_FEE` only.

2. **Rate budget is shared.** Track A holds the 0.4 rps nominal ceiling and runs
   at 0.286 rps for five hours of every six. If Track B ever needs venue contact
   it must take it **inside the inter-segment gaps** (≈05:00, 11:00, 17:00, 23:00
   UTC) so the aggregate never rises. `RATE_INCREASE = NO`. Track B's analysis is
   otherwise entirely offline against committed bytes.

3. **The cohort lost its only LIVE-state market.** `asc-epl-mnu-mnc-…-fh-neg-1pt5`
   retired on the venue's own `MARKET_STATE_EXPIRED`. It is **not substituted**.
   Any conclusion about live-market economics is therefore
   `NOT_OBSERVED_WITHIN_THE_FROZEN_COHORT`, and must be stated that way rather
   than generalised from pregame books.

---

## LIVE CAPITAL

`CURRENT_LIVE_CAPITAL_AUTHORIZATION = NO`. No capital is deployed automatically
under any result. If the evidence reaches A or a strong B, Track B returns a
*proposed* tiny live validation protocol for approval — whose purpose is to
measure real fill rate, queue behaviour, fees, adverse selection and pair
completion, **not** to make money on the first test.

`mirror_live = false` throughout.
