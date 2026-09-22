> **SUPERSEDED IN PART by `ECONOMIC_PACKAGE.md`.** Four conclusions in
> this document were wrong and are corrected there. Kept in place
> because the errors are instructive and because deleting the record of
> a withdrawn claim is not a correction.
>
> | row | this document said | corrected to |
> |---|---|---|
> | 5 | "UNRESOLVED — the strongest survivor", +0.0086/contract, positive 1,551 of 1,551 | **REJECTED on development evidence.** Run as whole episodes with inventory carried: **−0.0291/contract**, 95% CI [−0.140, +0.082] on 11 event clusters. q measured at **0.0753** against the 0.163 break-even |
> | 1 | "the headline returns are mostly this row, not the pair channel" | **WITHDRAWN.** That inferred a profit share from a capital share. Actual attribution: profit is **65.2% pair / 34.8% settlement** |
> | 7 | rejected because the pair channel "changes sign 3–3" | **rejected for a better reason.** A 3–3 count is weak evidence. The signal has already moved a median **+0.0100** against us when we see it (89.5% of 214,609 fills), and **0 of 214,609** are in a PMUS slug namespace |
> | 3 | "REJECTED on these books" | **narrowed.** A static one-book calculation rejects only the static version. The time-dependent version was then run over 8 horizon/size settings and is negative at all 8 |
>
> The pilot proposal at the end of this document is **replaced**: its
> stopping rule was invalid, its 500-episode cap was ~105× too coarse
> to detect the edge it targeted, and its $250 cap was defined against
> the wrong quantity. See `ECONOMIC_PACKAGE.md` §8–§9.

# Mechanism coverage — every case-study mechanism against our engine

One row per mechanism the team's research actually proposes. For each:
is it **implemented** in `bettor_policy_ev`, was it **evaluated**, and
what is the **verdict** — with the reason, not a label.

`EVALUATED` means the engine priced it on real recorded books.
`NOT EVALUABLE` means the engine refuses for a named missing input.

---

## The table

| # | mechanism | in the engine | evaluated | verdict | why |
|---|---|---|---|---|---|
| 1 | **Informed entry + hold to settlement** | `CROSS_BUY` / `HOLD` | **NO — not evaluable** | **UNRESOLVED** | the engine refuses both without `FV_BETTOR_INDEPENDENT`, and BETTOR has no identified fair value. Pricing it would mean inventing the input that decides the answer |
| 2 | **Taker complementary pair** | `CROSS_PAIR` | **YES**, 1,551 obs / 11 markets | **REJECTED** | `ask + (1−bid) = 1 + spread` is an identity. Costs par plus the spread before either taker fee. Median EV −0.0325/contract, profitable in 0 of 1,551 |
| 3 | **Maker entry + taker completion** (sequential inventory) | `QUOTE_BID` → `COMPLETE_PAIR` | **YES**, 1,551 obs | **REJECTED on these books** | median −0.0050/contract, positive in **0 of 1,551**. Buying at the bid and completing at 1−bid costs exactly par, so the pair returns zero gross and pays a taker fee. Distinct from row 2 and separately tested, as it must be |
| 4 | **Maker entry + cross out** (round trip, no price move) | `QUOTE_BID` → `CLOSE` | **YES**, 1,551 obs | **REJECTED as a strategy** | median −0.0055/contract, positive in 0 of 1,551. This is the *hurdle*, not a policy: it is what a round trip costs when nothing moves |
| 5 | **Rest on BOTH sides** (two-sided maker) | `QUOTE_BID` + `QUOTE_OFFER` | **YES**, 1,551 obs | **UNRESOLVED — the strongest survivor** | both legs fill: **+0.0086/contract median, positive 1,551 of 1,551**. One leg fills: −0.0050, positive 0 of 1,551. Break-even double-fill **q\* = 0.163 median, 0.515 max**. `q` is unmeasured |
| 6 | **Cross-venue PMUS/Kalshi** (Track P / Phase X) | not in this engine | **YES**, own holdout, 1,170 markets | **REJECTED, LOCKED** | +10.5% TRAIN → +0.5% VALIDATION → **−11.1% HOLDOUT**, 95% CI [−15.5%, −6.6%], negative **at zero fees**. Not to be retuned |
| 7 | **Copy a profitable account** (RN1, Ferrari, +4) | not in this engine | **YES**, 6 accounts | **REJECTED** | the pair channel **changes sign 3–3** across the six. 43.9%–91.9% of the capital behind every headline is *held to settlement*, not traded as pairs. RN1's own edge_roi CI **crosses zero** and decays +2.08% (37d) → +0.26% (7d) |
| 8 | **Feasible netting / capital release** | `COMPLETE_PAIR` vs flat round trip | **PARTLY** | **UNRESOLVED** | the *gross* is identical either way; the *capital* is not. Buy-at-bid/sell-at-ask releases immediately; buy-both-legs ties up 1−spread until the event resolves. Release mechanics on cancel and partial fill are **undocumented** and were never read back from the venue |
| 9 | **Adverse-selection–aware maker** (avoid toxic flow) | not implemented | **NO** | **UNRESOLVED** | needs a classifier for counterparty type. None exists, and the only adverse-selection number we hold is global-CLOB, ask-side-only and RN1-selected |

---

## What this changes about earlier claims

**Row 3 is the row I was told not to collapse into row 2, and it is
now tested separately.** It fails for its own reason — completing at
1−bid from an entry at bid costs exactly par, so the pair earns zero
gross and pays a taker fee — not because the taker pair fails. Same
verdict, different mechanism, separately evidenced.

**Row 5 exists because rejecting row 2 does not reject its mirror.**
The taker pair costs `1 + spread`; resting on both sides earns
`1 − spread`. One identity, two signs, and only the taker sign was
ever falsified. This is the only mechanism in the table that is
positive on recorded books in every single observation of its
favourable branch.

**Row 1 is the largest mechanism in the case studies and we cannot
price it at all.** Every account in the research holds a large
fraction of its capital to settlement — between 43.9% and 91.9% — so
the headline returns are mostly *this* row, not the pair channel. Our
engine refuses it for a named reason. That refusal is correct and it
is also the biggest hole in the coverage.

---

## The strongest remaining policy

**Row 5 — rest on both sides of a one-tick book.**

Feasible cash flows, from the engine, per contract, at 100-contract
fills:

```
both legs fill      +0.0086   median   (spread + two maker rebates)
one leg fills       -0.0050   median   (completion at par, taker fee)
break-even q*        0.163    median,  0.515 max
```

80.1% of observed books are one tick wide, where the engine quotes
**at the touch** and captures the full half-spread. 9.3% are two ticks
wide, where our own "improve by one tick" rule quotes **on the mid**
and captures nothing — a defect in our rule, not the venue's tick.

### What must be true for it to earn

1. **q > 0.163** on the books we actually quote — the double-fill rate
   in net cash.
2. The legs are **adversely correlated**: one fills precisely when
   price moves toward it, which is when the other will not. So `q` is
   not `p_fill²` and must never be modelled as independent.
3. Inventory from single fills is exitable at or better than the
   −0.0050 assumed here. A price move makes it worse.

### The smallest measurement that resolves it

**Rest two-sided quotes on a small set of one-tick markets and record
the realised net cash per quoting episode**, clustered by market:

- the outcome variable is **net cash**, not a mixture proportion and
  not a settlement return;
- the estimand is `E[net cash per episode]` with a clustered interval;
- it needs an order path and capital, and resolves in **execution
  time, not market time** — an episode ends when both legs fill, one
  leg is exited, or the quote is cancelled. No settlement wait.

This is the one measurement in the whole programme that is cheap in
elapsed time, because it does not wait for events to resolve.

### Pilot proposal — PREPARED, NOT REQUESTED

| | |
|---|---|
| objective | estimate `E[net cash per two-sided quoting episode]` with a clustered 95% interval that excludes zero, and measure `q` directly |
| universe | one-tick books only, from the frozen rule's open/valid set |
| size | the smallest fill that earns a non-zero rebate — **4 contracts** at mid 0.13, since 1–2 contracts round to zero |
| **loss budget** | **$250 hard cap**, enforced at the order path, not by policy |
| stopping rules | (a) cumulative loss reaches the cap; (b) the clustered interval excludes zero in either direction; (c) 500 episodes; (d) any single market contributes >20% of episodes |
| scaling criterion | scale only if the interval's **lower bound exceeds zero in net cash** — not if `q` exceeds a threshold, which is a different quantity |
| what it does NOT establish | venue capacity, behaviour at size, or anything about hold-to-settlement (row 1) |

**Not activated. Not requested here.** It needs an order path and
capital, both separately authorized, and this document asks for
neither.
