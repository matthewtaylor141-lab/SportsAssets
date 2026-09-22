# BETTOR — economic verdict on a prespecified evaluation

`BETTOR_EVALUATION_V1` · protocol frozen in commit `851cb23` · artifacts
`evaluation.json`, `eval_touches.json`

> **Status: DEVELOPMENT DIAGNOSTIC.** The independent-holdout claim this
> document originally made is withdrawn — see *Provenance* below. Nothing
> here qualifies or disqualifies a strategy.

**All figures below are SIMULATED replay on captured tape. None of them
are account results and no order was placed.**

---

## Provenance first, because it limits everything below

**This is NOT an independent holdout, and the earlier claim that it was
is withdrawn.** The September corpus — all of it, including 17–20
September — had already been inspected and used in earlier policy work:
`acceptance/policy_final.json` holds a C0/C2/C3/C4 sweep across four
queue fractions over the whole tape, and the C3 baseline carried into
this register was selected using it.

Committing a protocol on 2026-09-22 does not make dates examined before
it untouched. Freshness is a property of the data's history, not of the
document's. So every figure below is a **development diagnostic**.

Reading the later period once still limits how much *this* protocol
overfits it, which is worth keeping. It cannot undo the reading that
already happened.

## The screen, and what it does and does not settle

**No candidate passes the screen.** The threshold was fixed before the
result existed: net P&L after fees positive on the later period, with an
event-clustered 90% interval excluding zero.

The selected policy (`R10`) returned:

| queue scenario | episodes | fill | net | per capital-hour | 90% clustered interval |
|---|---|---|---|---|---|
| 0.00 | 197 | 32% | **−74.29** | −0.006605 | [−127.20, −12.10] |
| 0.25 | 211 | 14% | **−72.37** | −0.006030 | [−124.88, −12.17] |
| 0.50 | 209 | 11% | **−80.81** | −0.006520 | [−152.30, −5.35] |
| 1.00 | 210 | 9% | **−100.01** | −0.008021 | [−206.89, −7.59] |

Every interval excludes zero on the negative side. **That is not a
confident negative about the mechanism, and calling it one was an
overstatement that is withdrawn.** Five event clusters cannot settle the
question in either direction; a "confident positive" on the same sample
would have been wrong in the same way.

What these numbers support is narrow and still useful: **this policy, on
these five events, lost money, and the decomposition shows where it
went.** They do not establish that two-sided passive quoting on this
venue cannot work.

The earlier period had shown −3.29 worst case and +2.65 at the most
favourable queue assumption. The later figure is roughly twenty-five
times worse across 48 looks at one sample — consistent with the earlier
numbers being selection artifacts, which is what the split was built to
expose.

---

## What actually consumes the money — two costs, in tension

### 1. Completing a maker fill with a taker order costs about five times what resting earned

*This one does not depend on the split at all — it is arithmetic on the
published fee schedule plus an accounting identity over observed fills.*

Decomposing the baseline's filled development episodes (81 episodes,
4,459 contracts, queue fraction 0.00):

```
position P&L    +  4.358     the pair trade itself EARNS
maker rebates   +  6.330     the credit is real and was verified
taker fees      - 24.980     <- the entire loss, and more
--------------------------------------------------
net             - 14.292
```

Taker fees are 3.9× the rebates earned. That ratio is **not a property of
this sample** — it is the published schedule:

```
Θ_taker / |Θ_maker|  =  0.0600 / 0.0125  =  4.8    (JUL2026 regime)
                     =  0.0695 / 0.0125  =  5.6    (SEP2026 regime)
```

Any policy that completes every maker fill by crossing the book pays back
roughly five times what resting earned it, per contract, by construction.

> **A confound in the split, stated because it is real.** PMUS raised
> Θ_taker from 0.0600 to 0.0695 at **2026-09-17T04:00Z**, four hours after
> the DEV/EVAL cut. Both dates come from the calendar and neither was
> chosen with reference to the other, but the consequence is that DEV runs
> almost entirely under the old rate and EVAL almost entirely under the
> new one. The replay applies the regime by fill timestamp
> (`bettor_policy_ev.theta_taker_at`), so the arithmetic is right; the
> comparison is what carries the confound.
>
> **It cannot explain the result.** EVAL's taker fees were −21.81 against a
> position loss of −63.91. A 16% higher taker rate accounts for roughly
> $3 of a $74 loss, and none of the $63.91.

Variants R7–R11 stop paying the taker, and every one of them improves net:
worst-case net goes from −14.29 (baseline) to −3.29 (R10).

### 2. Those taker fees were an insurance premium, and the later period contained the claim

On the later period the loss is **not** the fee:

| | position | rebates | taker fees | net |
|---|---|---|---|---|
| earlier (qfrac 0.00) | +7.36 | +6.60 | −11.31 | **+2.65** |
| later (qfrac 0.00) | −63.91 | +11.43 | −21.81 | **−74.29** |

Two episodes account for the entire loss:

| episode | opened | outcome | net |
|---|---|---|---|
| `aec-cfb-uwg-etnst-2026-09-19` | 17:58:07Z | FLAT_EXITED_MAKER | **−46.60** |
| `aec-cfb-coast-del-2026-09-19` | 18:21:42Z | CARRIED_SETTLED, 100 YES → 0.00 | **−33.22** |
| *the other 195 episodes* | | | **+5.53** |

Both were opened **during live college football play**. The two protections
R10 removed in order to save taker fees — cancelling the second side on a
fill, and flattening before expiry — are precisely what had been preventing
that. R10's development improvement was the insurance premium refunded in a
period that happened to contain no claim.

**These two costs are in direct tension and nothing in the register of
fifteen variants resolves them.** Pay the taker and the fees exceed the
edge; don't pay it and a single adverse move exceeds every fee saving in
the corpus.

---

## The branch that failed, kept as a negative result

The first hypothesis was that the binding constraint is the **fill rate**:
358 of 420 episodes never fill, and 88% of committed capital-hours rest
behind quotes that are never reached. `R4–R6` gate entry on whether the
book's own trailing flow can reach the quote.

The gate worked exactly as designed, and the design was wrong:

| | fill rate | loss per contract, filled episodes |
|---|---|---|
| R0 baseline | 39% | −0.0032 |
| R4 cover ≥ 1 | 78% | −0.0045 |
| R5 cover ≥ 3 | — | −0.0048 |

Committed capital-hours fell eleven-fold and the loss got *worse* per
contract. Books that trade enough to reach a resting quote are books whose
price is moving. Raising the fill rate raises exposure to the losing subset
rather than improving it. **It is preserved in the register and is not
retuned.**

Separately: never-filled episodes contribute exactly **0.00** net. The
entire P&L, positive and negative, comes from episodes that fill. The fill
rate was never the lever.

---

## What would resolve it, and why it cannot be resolved here

The loss is the **adverse move**, so the rule that addresses it declines to
quote into one. Time-to-resolution is the natural input and **it is not in
the captured corpus**: the tape carries no close time or event-start time,
only a state transition that arrives after the fact.

Trailing realised volatility *is* in the corpus, is strictly backward
looking, and prices the same thing:

```
cover = spread / (expected absolute move over the quoting horizon)
```

A mechanism check against the two loss episodes — recorded as a diagnostic
read in `eval_touches.json`, because it touched the evaluation split:

| episode | spread | expected move over 2400s | cover | decision |
|---|---|---|---|---|
| uwg-etnst | 0.015 | 0.1134 | **0.132** | STAND_ASIDE |
| coast-del | 0.010 | 0.1162 | **0.086** | STAND_ASIDE |

The gate refuses both by a wide margin — the expected move is 7× to 12× the
spread — so the refusal is not threshold-sensitive anywhere in the range
tried, and no threshold was tuned to produce it.

**This establishes the mechanism and nothing more.** It does not establish
that the policy is profitable. Two facts prevent that claim:

1. **The evaluation split is spent.** It was read once, by R10, as the
   protocol requires. Running R14 against it now would convert a held-out
   set into a development set, and `bettor_evaluation.py` refuses a second
   touch in code rather than in a note.
2. **The development split cannot validate it either.** The volatility the
   gate exists to refuse did not occur during the development period — the
   gate removes only 14 of 210 episodes there. A rule cannot be validated
   on data containing none of the condition it targets.

---

## What the sample can and cannot support, in any case

The corpus is **420 episodes over FIVE events**. Episodes inside one event
share a book, a settlement and a direction, so the effective sample size for
any economic claim is five, not four hundred. Every interval above is a
bootstrap over events resampled whole, and with five clusters no interval
can be narrow. A *positive* result on this corpus could only ever have been
suggestive. The negative one is clearer only because its point estimate is
far from zero.

---

## The exact remaining experiment

**Collect a period containing live event resolution, and evaluate the
volatility gate on it once.**

- **Instrument:** the observation release already prepared. It subscribes to
  the live ladder, and the incentives API it reads carries `eventStartTime`
  — the time-to-resolution input the captured corpus lacks entirely.
- **Prespecification:** the gate, its threshold and the acceptance rule are
  already fixed in `bettor_policy.RULES["volatility"]` and the register.
  Nothing is left to choose after the data arrives.
- **Sample requirement:** the binding constraint is events, not episodes. A
  week of five markets buys five clusters again. A defensible interval needs
  **twenty or more independent events**, which at the current capture rate
  is a month of collection, not a week.
- **What it cannot do:** produce evidence of *live profitability*. That
  requires resting real orders and observing real fills. The replay cannot
  see our own quote's effect on the book, and no amount of captured tape
  fixes that.

**Nothing here authorises funded orders, a trading-control change, credential
movement or a production deployment.**
