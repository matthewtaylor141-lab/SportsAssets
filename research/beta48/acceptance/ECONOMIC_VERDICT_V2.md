# BETTOR — economic verdict on a prespecified evaluation

`BETTOR_EVALUATION_V1` · protocol frozen in commit `851cb23`, before the
evaluation split had ever been read · artifacts `evaluation.json`,
`eval_touches.json`

**All figures below are SIMULATED replay on captured tape. None of them
are account results and no order was placed.**

---

## The verdict

**No candidate qualifies.** The threshold was fixed before the result
existed: net P&L after fees positive on the evaluation split, with an
event-clustered 90% interval excluding zero.

The selected policy (`R10`) returned, on data it had never been fitted to:

| queue scenario | episodes | fill | net | per capital-hour | 90% clustered interval |
|---|---|---|---|---|---|
| 0.00 | 197 | 32% | **−74.29** | −0.006605 | [−127.20, −12.10] |
| 0.25 | 211 | 14% | **−72.37** | −0.006030 | [−124.88, −12.17] |
| 0.50 | 209 | 11% | **−80.81** | −0.006520 | [−152.30, −5.35] |
| 1.00 | 210 | 9% | **−100.01** | −0.008021 | [−206.89, −7.59] |

Every interval excludes zero **on the negative side**. This is not an
inconclusive result. It is a confident negative.

Development had shown −3.29 worst case and +2.65 at the most favourable
queue assumption. The out-of-sample figure is roughly twenty-five times
worse. That gap is itself the finding: the development numbers were
selection artifacts across 48 looks at one sample, and the protocol
existed to expose exactly that.

---

## What actually consumes the money — two costs, in tension

### 1. Completing a maker fill with a taker order costs about five times what resting earned

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
Θ_taker / |Θ_maker|  =  0.06 / 0.0125  =  4.8
```

Any policy that completes every maker fill by crossing the book pays back
roughly five times what resting earned it, per contract, by construction.
Variants R7–R11 stop paying the taker, and every one of them improves net:
worst-case net goes from −14.29 (baseline) to −3.29 (R10).

### 2. Those taker fees were an insurance premium, and the evaluation period contained the claim

On the evaluation split the loss is **not** the fee:

| | position | rebates | taker fees | net |
|---|---|---|---|---|
| DEV (qfrac 0.00) | +7.36 | +6.60 | −11.31 | **+2.65** |
| EVAL (qfrac 0.00) | −63.91 | +11.43 | −21.81 | **−74.29** |

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
