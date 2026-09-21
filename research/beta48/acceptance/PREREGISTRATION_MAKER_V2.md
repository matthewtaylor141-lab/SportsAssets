# PRE-REGISTRATION — MAKER v2

**FROZEN 2026-09-21, before any v2 evaluation was run.** v1 is preserved
unchanged in `PREREGISTRATION_MAKER_V1.md`; nothing here retunes it.

## 0. WHY v2 EXISTS, AND WHY THAT IS NOT A LICENCE TO RELAX ANYTHING

v1 was frozen with a **hypothetical** fee schedule (taker 0.02/contract flat,
maker 0.01/contract **as a charge**). The published schedule is now
implemented and wired: `PMUS_PUBLISHED_2026_09_17`, `fee = θ·C·p·(1−p)`,
θ_taker +0.0695, **θ_maker −0.0125, a REBATE**. One input changed and the
maker term's **sign** flipped.

Two things follow, and they pull in opposite directions:

1. **v1's numbers are superseded**, because they were computed with a term
   that was wrong in shape and in sign. They are preserved, not deleted.
2. **A corrected input is not evidence.** The evaluation whose conclusion
   moves when one assumption is fixed was never measuring an edge; it was
   measuring the assumption. v2 must therefore be **stricter**, not looser,
   than v1 — a protocol rewritten after seeing a favourable number is not a
   pre-registration.

**Every acceptance rule below is at least as strict as v1's.** Where v2 adds
a requirement it is named as an addition. No v1 requirement is removed.

## 1. WHAT IS BEING TESTED

**M1** — a passive quote at the touch, held and then exited.
**M2** — a maker first leg plus completion of the complement.
**I1** — the completion-vs-exit policy.

Unchanged from v1.

## 2. THE MODEL, AND EVERY TERM'S SOURCE

```
round_trip_per_contract = (exit_price − entry_price)          [BUY]
                        − round_trip_fee
                        + verified_rebates
                        − carry × duration
```

`exit_price` is built from the **moved reference**, so adverse selection
enters **once**, as `conditional_reference_move`. There is no second
subtraction anywhere. (This is v1's correction to the withdrawn half-spread
model, and it stands.)

| term | source | status |
|---|---|---|
| `entry_price` | the observed bid (BUY) | **OBSERVED** |
| `exit_price` | built from the moved reference and `exit_spread` | derived |
| `exit_spread` | observed at entry | **OBSERVED** (assumed unchanged at exit — stated as an assumption) |
| **`round_trip_fee`** | **`PMUS_PUBLISHED_2026_09_17`, both legs at their OWN prices, rounded per fill at the stated clip size** | **PUBLISHED** — new in v2; v1 used a flat hypothetical |
| `verified_rebates` | a settled statement | **ZERO.** No incentive enters a base case until seen on a statement. |
| `carry`, `duration` | — | `NOT_IDENTIFIED` |
| **`conditional_reference_move`** | — | **`NOT_IDENTIFIED`** |
| **`p_fill` (entry)** | — | **`NOT_IDENTIFIED`** |
| **`p_fill` (passive exit)** | — | **`NOT_IDENTIFIED`** — *new in v2; see §4* |

## 3. EVIDENCE PRECEDENCE — THE WEAKEST SOURCE WINS

`NOT_IDENTIFIED` < `HYPOTHETICAL` < `PUBLISHED` < `MEASURED`.

`PUBLISHED` sits strictly between hypothetical and measured: nobody invented
it, and nobody has seen it applied to this account. **A result carrying a
PUBLISHED fee and a HYPOTHETICAL move is HYPOTHETICAL.** Mixing a published
term into an assumed grid does not upgrade the grid. Enforced in
`bettor_maker_economics.round_trip`, not left to the reader.

## 4. THE PASSIVE EXIT NEEDS ITS OWN FILL PROBABILITY — NEW IN v2

v1's grid ran `EXIT_AGGRESSIVE` and `EXIT_PASSIVE`, and the passive row is
where the published rebate makes the arithmetic positive — both legs earn a
rebate instead of one earning and one paying.

**That row assumes the exit quote fills.** It is a *second* resting order and
therefore a *second* unmeasured fill probability, conditional on the first
having filled and on the market having moved to wherever it moved. Reporting
it beside the crossing row as though the two were equally supported is the
same error as pricing an unverified fee at zero.

**Rule: `EXIT_PASSIVE` results carry TWO `NOT_IDENTIFIED` terms and may never
be reported without both named.** Under §6 rule 2 that makes M1-passive
`UNRESOLVED` regardless of its sign.

## 5. THE FROZEN GRID

Because `conditional_reference_move` and both `p_fill` terms are
`NOT_IDENTIFIED`, every book is evaluated across a grid rather than at a
chosen value. **The grid is frozen here and a result outside it is not
reported.**

| dimension | values |
|---|---|
| `conditional_reference_move` | 0.000, −0.005, −0.010, −0.020 |
| exit route | `EXIT_AGGRESSIVE`, `EXIT_PASSIVE`, `EXIT_SETTLEMENT` |
| entry `p_fill` | 0.01, 0.05, 0.20 |
| exit `p_fill` (passive route only) | 0.20, 0.50, 1.00 |
| **clip size** | **1, 5, 10, 100 contracts** |
| carry | 0.0 |

**Conservative corner:** move −0.020, entry `p_fill` 0.01, exit `p_fill` 0.20.

**Clip size is a grid dimension in v2 and was not in v1.** Fees round to the
cent **per fill**, so the per-contract fee is not constant in size: at
p = 0.485 a round trip costs 0.02000/contract at 1, 0.01000 at 2, and ~0.0142
from 10 upward. A per-contract figure quoted without its clip size is not a
number. `EXIT_SETTLEMENT` was declared in v1 and **not run**; it is run in v2
or its absence is recorded as a deviation.

## 6. ACCEPTANCE RULES — UNCHANGED FROM v1, PLUS ONE

A candidate is **SUPPORTED** only if **all** hold:

1. Net cash per contract is **positive at every grid point**, not merely at
   the favourable ones.
2. **No term it depends on is `NOT_IDENTIFIED`.** A candidate needing a
   `NOT_IDENTIFIED` term is `UNRESOLVED` — never SUPPORTED, and never
   REFUTED either.
3. The sample is **≥ 200 filled contracts across ≥ 50 distinct contracts**.
4. A 95% interval, clustered by event, excludes zero.
5. **NEW:** the fee term is `VERIFIED_APPLIED`, not merely `PUBLISHED`. A
   strategy whose profitability depends on a rebate nobody has seen paid is
   a strategy contingent on the rebate.

**Rule 2 is why the published schedule cannot promote anything.** It moves
one term from HYPOTHETICAL to PUBLISHED and leaves three NOT_IDENTIFIED. The
verdict for M1 is `UNRESOLVED` before the first number is computed, and
rule 5 keeps it there even if the others were satisfied.

## 7. WHAT COUNTS AS EVIDENCE

| class | admissible for |
|---|---|
| `SYNTHETIC_SCENARIO` | code paths only. **Never** an economic claim. |
| `REPLAY_DECISION` | that the engine decides on real data without fabricating inputs. |
| `REPLAYED_OBSERVATION` | book statistics. **Not** fills — BETTOR has never rested an order. |
| `PROSPECTIVE_SHADOW` | forward-looking claims about decisions, not about fills. |
| `LIVE` | fills, `p_fill`, markout, fee application. |

**Only `LIVE` can settle rules 2, 3, 4 or 5.** Every other class can refute a
mechanism or demonstrate a path; none can support an edge.

## 8. DEVIATIONS ARE RECORDED, NEVER RETROFITTED

v1's evaluation declared routes `{AGGRESSIVE, SETTLEMENT}` and a `p_fill`
grid, then ran `{AGGRESSIVE, PASSIVE}` with no `p_fill` grid — it did not
implement its own protocol, and the "REFUTED" verdict it produced was
withdrawn to a scenario result. **That is the failure mode this section
exists to prevent.** Any v2 run that departs from §5 states the departure in
its own output before its numbers, and its verdict is `PROTOCOL_DEVIATION`,
not a result.

## 9. WHAT IS ALREADY KNOWN AND MUST NOT BE RE-LITIGATED

- **Class C — FALSIFIED.** Taker complementary pairs: 3,732 observations, 0
  at or below par, median basis 1.0400, minimum 1.0050. `ask + (1 − bid) =
  1 + spread` is an identity.
- **Class D — FALSIFIED AND LOCKED.** Phase X cross-venue: −11.1% HOLDOUT,
  95% CI [−15.5%, −6.6%].
- **Track P — preserved permanently as a negative result. Must not be
  retuned.** Negative at zero fees, so no fee correction revives it — and the
  published schedule is not a reason to revisit it.
- **Finding B-1 — locked.** A taker/taker complementary pair costs
  `1 + spread`.
- **BLOCK_4 — binding.** 22,297 displayed shares against 180 traded in 16
  minutes, zero touches. Displayed depth is not executable depth.

**The corrected fee schedule does not reopen any of these.** Track P is
negative at zero fees; Class C and Finding B-1 are identities; Class D and
BLOCK_4 are measurements of things fees do not enter.

## 10. THE PREDICTION, STATED BEFORE THE RUN

**M1 will be `UNRESOLVED` under rule 2** — it needs three `NOT_IDENTIFIED`
terms. **M2 will be `UNRESOLVED — BLOCKED ON CAPABILITY`**, since
`holds_both_legs_independently` is UNKNOWN and, per the 2026-09-21 findings,
the capture holds no leg-level venue identifier with which to establish a
complement at all. **I1 will remain SUPPORTED AS A POLICY, NOT AS AN EDGE.**

Recorded here so that if the run returns anything else, the difference is
visible rather than absorbed.
