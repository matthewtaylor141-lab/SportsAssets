# What B, C and D actually require

Owner 2026-09-20: *"'Not identifiable from these feeds' should become a
concrete dependency list."*

Three evidence kinds are kept apart throughout, because conflating them
is how a simulation becomes a claim:

| kind | what it is | what it can support |
|---|---|---|
| **PUBLIC_BOOK** | what the venue published, read read-only | state economics (A) |
| **SIMULATED_FILL** | a counterfactual about an order that never existed | nothing on its own; a sensitivity bound at best |
| **ACTUAL_FILL** | an execution of ours, admitted by the venue | B and C directly |

---

## B — `P(OUR RESTING ORDER FILLS | STATE, QUOTE)`

| dependency | status | offline now? | needs authorization? |
|---|---|---|---|
| Queue position at insertion | **UNAVAILABLE** — the book feed is a snapshot, not an order-event stream | no | no venue access would fix it; needs a feed that does not exist |
| Queue depletion split into trades vs cancels | **UNAVAILABLE** — tape volume is market-wide and sideless; the mix is not recoverable by subtraction | no | — |
| Hidden/iceberg liquidity | **UNAVAILABLE** | no | — |
| Price-time priority semantics | **UNDOCUMENTED** for this venue | **YES** — venue docs review | no |
| Our own admitted fills | **NONE EXIST** | no | **YES** — order path + capital |

**Preparable offline now:** the priority-semantics question, and a
simulated-fill sensitivity harness that reports a *range* under stated
queue assumptions — clearly labelled `SIMULATED_FILL`, never a P_FILL.

**Blocking dependency:** `ACTUAL_FILL` evidence. Nothing in the public
feeds substitutes for it. This is the same conclusion the evidence
matrix reached; it has not moved.

---

## C — `E[SETTLEMENT − QUOTE | OUR RESTING ORDER FILLED, STATE]`

| dependency | status | offline now? | needs authorization? |
|---|---|---|---|
| Everything B requires | see above | — | — |
| Settlement per instrument | **AVAILABLE** once markets resolve | yes, wait | no |
| Settlement semantics (which side settles at 1) | **NOT_VERIFIED** | **YES** — venue docs + a resolved market | no |
| The conditioning event itself | **NEVER OCCURS** without our fills | no | **YES** |

**C is strictly downstream of B.** The conditioning event is our own
execution; a dataset that never contains one cannot estimate a quantity
conditional on it. The unselected capture does **not** narrow C — it
identifies A, which is a different estimand.

**Preparable offline now:** settlement-semantics verification, and the
estimator itself written and tested against synthetic input, so that the
day fills exist the analysis is not also being invented.

---

## D — Maker EV

| component | status | source once available |
|---|---|---|
| P_FILL | NOT_IDENTIFIED | B |
| VALUE_IF_FILLED | NOT_IDENTIFIED | C |
| VALUE_IF_NO_FILL | NOT_IDENTIFIED | option value of the unfilled branch — modellable offline |
| FEES | **VERIFIED** | `Θ_taker +0.06`, `Θ_maker −0.0125`, banker's rounding per fill |
| REBATES | **VERIFIED** | max 0.003125/share at p=0.50 |
| INVENTORY CONSEQUENCES | NOT_IDENTIFIED | exit engine output; exit dataset has 0 rows |

Two of six are verified, and they are the two that need no fills. D
cannot be assembled while four are unidentified, and `combine()` refuses
to produce a number rather than treating an unknown as zero.

---

## What the unselected capture does and does not move

It identifies **A** and builds the clean state frame for toxicity and
fair-value work. It moves **B, C and D not at all**. Anyone reading a
coverage improvement as progress toward maker EV has substituted A for
C, which is the substitution the whole naming discipline exists to
prevent.

**The honest summary:** B and C are blocked on evidence that only real
executions produce. That is a statement about what would be required,
**not** a proposal. No order path exists, no capital is authorized, and
none is being requested here.
