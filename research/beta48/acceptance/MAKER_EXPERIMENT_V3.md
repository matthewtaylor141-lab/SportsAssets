# MAKER EXPERIMENT v3 — hypotheses, protocol, and the holdout

**FROZEN 2026-09-21, before any v3 data was collected.**
`PREREGISTRATION_MAKER_V1.md` and `_V2.md` are preserved unchanged.
Nothing below retunes either.

---

## 0. THE FINDING THAT SHAPES THIS EXPERIMENT

The directive asks me to translate the RN1/Ferrari research into
hypotheses about entry, completion, exits and capital recycling. Doing
that honestly produces an uncomfortable result, and it belongs first:

> **RN1 was the TAKER. Against him, an anonymous resting offer lost
> −$0.0090/share net, 95% CI [−0.0143, −0.0038], MARKED TO SETTLEMENT.**
> — 112,553 trades over 9,337 conditions, clustered by condition, fills
> observed not modelled, **Polymarket CLOB — a different venue**,
> 2026-08-06..09-11, **one counterparty**, and the repository's own
> verdict on it is `THIS_IS_ACTUAL_BETTOR_ADVERSE_SELECTION = NO`.
> (`BETA48_CLOSEOUT.md` §5.2; full provenance `MAKER_ENGINE_GATE_V2.md`.)

**THAT IS A SETTLEMENT PAYOFF, NOT A MARKOUT, AND NOT A REALIZED P&L.**
The three are different quantities and I used the third-hand one to
argue about the first. A settlement mark includes the whole path to
resolution; a markout includes sixty seconds of it; a realized P&L is
cash from a round trip that actually happened. **One counterparty on
one other venue, marked to settlement, does not establish that every
maker policy loses** — see `CORRECTIONS_ACTIVATION.md` §1.

RN1: 78,500 merges, "pure taker", "informed entry". **He did sell** —
234 sells, $583,580 stake, 0.153% of activity, `SELL_PNL` **+$370,281,
ROI +63.4%**; the closeout table's "zero sells" is wrong and the audit
has the figures. Ferrari: pair-positive, ρ −1.000, best total ROI
**+7.06%** — and under `MAKER / REBATE`, **"none observed."**

**Neither account earned a maker rebate. Both made money by crossing
the spread against somebody who was quoting.**

So the naive translation — "RN1 did well, quote like RN1" — is
unsupported: he was on the other side of the trade from the policy it
would have us run, and the one quoter we can see facing him lost money
over that window on that venue.

**That is a reason to design the experiment around adverse selection.
It is not evidence that our quoting loses.** One anonymous counterparty
against one informed taker, on a different venue, marked to settlement,
is a single observation of a single pairing. It bounds nothing about a
selected universe, our clip size, our 300-second cancel, or PMUS.

What it does is set the experiment's subject:

> **H0: BETTOR cannot passively quote into this venue's flow without
> being adversely selected at a rate exceeding the spread plus the
> rebate.**

Everything below is built to give H0 a fair chance to survive — and to
give it a fair chance to FAIL, which is the point. The evidence
currently neither favours nor refutes it for BETTOR, because BETTOR has
never rested an order.

## 1. VENUE DIFFERENCES THAT BREAK THE TRANSLATION

`WHALE_NATIVE_EV_BRIDGE_V1.md` §7 already grades these, and
`SAME_PAYOFF_DOES_NOT_IMPLY_SAME_EXECUTION = True` is its conclusion.

| Mechanism | Equivalence | What actually differs |
|---|---|---|
| RESIDUAL_INVENTORY_RISK | STRONG | one-sided exposure is one-sided exposure |
| COMPLETION | **PARTIAL** | legacy Polymarket: two tokens, complete by holding both and **merging**. PMUS: one book per market with a long and a short side. Same payoff, different implementation, fill behaviour and queue mechanics. |
| TWO_SIDED_PASSIVE_MM | **PARTIAL** | the archive's makers are not on PMUS's book |
| CAPITAL_RECYCLING | **PARTIAL** | RN1 recycles via merge/redeem. On PMUS **institutional** `capital_recycling()` reports the chain breaking at `MERGE_OR_NET`, so whether completing a pair returns capital is **`NOT_IDENTIFIED` — not `NO`**. On **retail** the venue nets at the second fill and capital returns *immediately with no merge call at all*, which is direct evidence that a missing merge action does not imply missing recycling. |
| HEDGE_INVENTORY_CLOSE | NOT_IDENTIFIED | no external hedge instrument established for PMUS |

**The capital-recycling row is the one that bites, and I had it
wrong.** I read `merge_permitted()` returning `permitted=False` as
"capital never comes back". `bettor_merge.py` §12 warns against exactly
that reading — *"reading the first as the second gets RETAIL exactly
backwards"* — and `capital_recycling(INSTITUTIONAL)` gives the real
answer: `CAPITAL_RECYCLING_AVAILABLE = NOT_IDENTIFIED`, *"not zero and
not NO: either would be a claim about the venue we have not
established."* Establishing it is a named task, not a settled fact.

## 2. FOUR HYPOTHESES, EACH WITH ITS MEASUREMENT PATH

Every one is falsifiable and every input has a named way to measure it.
Where an input is `NOT_IDENTIFIED`, the path is stated — the directive
requires that and it is also the only thing that stops this becoming a
wish list.

### H1 — ENTRY. Selective quoting beats indiscriminate quoting.

> Restricting quotes to markets selected by liquidity and activity
> produces a **higher fill rate at a less adverse markout** than
> quoting the same size across an unselected sample.

*Why it might be true:* the measured spread distribution has p90 =
**0.9400** — the top decile of books is untradeable, and those are
exactly the books where a passive order waits longest.
*Why it might be false:* volume attracts informed flow. The markets
most likely to fill our quote are the ones most likely to fill it
because someone knows something.
*Measurement:* `p_fill` and markout, split by universe membership.
The universe rule is frozen in `bettor_universe.py` and its version
moves if any parameter does, so the two arms stay comparable.
*Status of inputs:* `p_fill` NOT_IDENTIFIED — **path: the pilot.**

### H2 — ADVERSE SELECTION. The 0.90¢ is not a law of nature.

> Post-fill markout at 60 s, conditional on our fill, is **not more
> adverse than the spread plus the rebate** in the selected universe.

**The threshold is our own economics, not the 0.90¢.** That figure is a
settlement-marked loss for an anonymous quoter against one counterparty
on another venue; using it as a bar would be comparing a markout to a
settlement payoff. It is cited as a reason to take adverse selection
seriously and as an order of magnitude, never as our prior.
*Why it might be true:* we quote in a selected universe, at a size
below the touch, and cancel at 300 s. RN1's counterparties quoted into
whatever he hit.
*Why it might be false:* it is the same kind of flow.
*Measurement:* midpoint at fill+60 s minus midpoint at fill, on our
fills only. **Never** combined with quote-to-fill movement — those
answer different questions and adding them double-counts, which is the
same mixed-reference error that made the withdrawn maker model report
−0.015 on a round trip whose cash P&L was zero.
*Status:* NOT_IDENTIFIED — **path: the pilot, from our own fills.**

### H3 — COMPLETION. A filled leg can be completed at a basis below par.

> Given a maker fill on one side, the complement is executable at a
> price making `own_basis + complement_ask < 1.00` more often than not,
> within 300 s.

*Why it might be true:* it is what RN1 and Ferrari did 78,500 times.
*Why it might be false — and this is the strong reason:*
`holds_both_legs_independently` is **UNKNOWN** on the institutional
account, and if buying the complement NETS rather than acquires, the
cash flow is a CLOSE and the par model does not describe it at all.
Worse, the capture holds **no leg-level venue identifier**:
`instrument_id` equals `market_id`, `condition_id` is `NOT_IDENTIFIED`
on all 1,538 rows, and `no_ask`/`no_bid` are the sentinel on 100% of
rows. **We cannot currently identify a complement to buy.**
*Measurement:* `reconcile_read.capability_probe()` for the capability;
a second stream subscription on the sibling slug for the quote.
*Status:* **BLOCKED, not merely unmeasured** — path §5.

### H4 — RECYCLING. Capital duration, not return per trade, binds.

> Working capital required for a given daily executed notional is set
> by the **holding period**, and the holding period on PMUS is longer
> than RN1's because his recycling mechanism (merge/redeem) is not
> available to us.

*Measurement:* fill timestamp → flat timestamp, per contract.
*Status:* **no BETTOR-native strategy has ever had a fill, and no
BETTOR order has ever rested.** (The firm's ledger *does* hold 52
filled rows carrying $16,180.53 from the mirror lane — "zero fills
anywhere" was wrong; the scope that matters is BETTOR-native.) So no
BETTOR holding period has been measured. RN1's is, on another venue:
t25 = 120 s, **t50 = 600 s**, t75 = 1800 s, with 76.51% exiting by
settlement and 23.49% of first legs never completing — a reference
point, not a transfer. **Path: the pilot.**

## 3. WHAT IS EVALUATED, AND HOW

One cash-flow model, every term sourced:

```
round_trip = (exit_price − entry_price) × qty
           − round_trip_fee(schedule, entry, exit, route, qty)
           + verified_rebates          [ZERO until seen on a statement]
           − carry × duration
```

`conditional_reference_move` enters **once**, through the exit price.
Adverse selection is not a separate subtraction.

| term | source | status |
|---|---|---|
| entry / exit price | observed book | OBSERVED |
| `round_trip_fee` | `PMUS_PUBLISHED_2026_09_17`, both legs at their own prices, rounded per fill **at the stated clip size** | PUBLISHED |
| `verified_rebates` | a settled statement | **ZERO** |
| `conditional_reference_move` | — | NOT_IDENTIFIED → H2 |
| entry `p_fill` | — | NOT_IDENTIFIED → H1 |
| exit `p_fill` (passive route) | — | NOT_IDENTIFIED → H1 |
| `duration` / carry | — | NOT_IDENTIFIED → H4 |

**Standard rebates, conditional incentives and account eligibility are
three different things.** The published −0.0125 maker θ is a *standard*
term and enters as PUBLISHED. Any promotional or volume-tier incentive
is *conditional*, enters as **zero**, and `volume_tier` stays
`NOT_ESTABLISHED`. Whether this account is eligible for either is a
third question, answered only by a settled statement.

**A one-contract fill at $0.50 earns a rebate that rounds to zero.**
Measured on the published schedule: `min_contracts_for_a_cent` is **2**
at p=0.50 and **9** at p=0.05. Round trip at p=0.485, maker in/taker
out: **0.02000/contract at 1**, **0.01000 at 2**, **~0.0142 from 10
up**. The per-contract economics at 1 and 2 contracts are
**non-monotone** and neither is the asymptote. **A pilot at one
contract measures a size nobody would trade, and its result may not be
extrapolated to larger fills.** Hence 5 contracts (§4).

## 4. THE HOLDOUT PROTOCOL — FROZEN BEFORE COLLECTION

| | |
|---|---|
| Universe | `bettor_universe` V1, frozen. Ranked by traded volume. |
| Quote size | **5 contracts** — the smallest clip whose per-contract fee is within 1.5% of the asymptote at every eligible price |
| Price cap | ≤ $0.50 |
| Quote lifetime | **300 s**, then cancel. Part of the `p_fill` definition, not an operational detail. |
| `p_fill` | P(a 5-contract quote at the touch fills **in full** within 300 s, before cancellation). Denominator = quotes **acknowledged by the venue**. A cancelled quote is a completed observation with outcome NO_FILL, never a dropout. |
| `p_fill_partial` | reported **beside** it, never added to it |
| Combined exposure | one limit over filled inventory + live quotes + cancel-pending + recovery commitments (`ShadowLoop.combined_exposure`) |
| Loss budget | pre-trade: realized loss + worst case outstanding (`max_worst_case_loss`) |
| Sample | 200 filled contracts across ≥ 50 distinct contracts |
| **STOP** | first of: 200 filled contracts; 14 days; the next order would breach the loss budget |
| **HALT** | capability change, gate unreadable, >20% freshness failures in 1 h, any reconciliation residual ≠ 0, a cancel unacknowledged > 60 s. A HALT persisting 24 h becomes a STOP. |
| Early stop for success | **NOT PERMITTED** |

**Every eligible quote opportunity is recorded**, including the ones we
declined: market, reason, book at decision. Then order sent, venue
acknowledgment **or rejection with its reason**, resting, partial fill,
full fill, cancel requested, cancel confirmed, and **time at risk**
(ack → flat). A pilot that recorded only its fills could not compute a
fill *rate*, because the denominator would be missing.

### Acceptance — unchanged from v2, plus one

A candidate is **SUPPORTED** only if all hold:

1. Net cash per contract positive at **every** grid point.
2. **No term it depends on is NOT_IDENTIFIED.**
3. ≥ 200 filled contracts across ≥ 50 contracts.
4. A 95% event-clustered interval excluding zero.
5. The fee term is `VERIFIED_APPLIED`, not merely `PUBLISHED`.
6. **NEW — H2 must be rejected on our OWN markout.** If post-fill
   markout is not distinguishably better than the spread plus the
   rebate, no fill rate saves the strategy. **Fill probability alone
   cannot establish profitability**; fill behaviour and markout are
   evaluated **jointly** or not at all. The 0.90¢ is not the bar —
   it is a settlement-marked figure from another venue and another
   counterparty.

## 5. WHAT BLOCKS EACH HYPOTHESIS TODAY

| hypothesis | blocker | path |
|---|---|---|
| H1 entry | `p_fill` needs our own resting orders | funded pilot |
| H2 adverse selection | markout needs our own fills | funded pilot |
| H3 completion | **no leg-level venue identifier in the capture**; `holds_both_legs_independently` UNKNOWN | `capability_probe()` (built; blocked on a credential) + a sibling-slug subscription |
| H4 recycling | zero fills exist, so no holding period | funded pilot |

**Three of four need the pilot and the fourth needs a credential.**
That is the honest state, and it is why this document is a protocol
rather than a result.

## 6. THE PREDICTION, RECORDED BEFORE THE RUN

Under §3's rules, with three terms NOT_IDENTIFIED and the fee term
PUBLISHED rather than VERIFIED_APPLIED, **every candidate will be
UNRESOLVED**. H3 will additionally be BLOCKED ON CAPABILITY.

Recorded here so that if a run returns anything else, the difference is
visible rather than absorbed.
