# Can these contracts be valued at all? The bound, and what it costs

Written 2026-09-25 against SHA `905c05e` (live), the live venue prose, the
captured bookmaker rules, and the measured census of run 66
(`command-verify` #66, job `entry-run`, 6 h window, 145 candidates).

**The answer, first.** No. And there are two separate reasons, which have to
be kept apart because they have different remedies:

1. **The rigorous bound is vacuous.** With only evidenced quantities, the
   contract's value is bounded by `V ∈ [0, 1]`, which admits both signs of
   net edge and therefore establishes nothing. §3.
2. **The most favourable identification the connected source can even name
   is already measured, and it is negative.** Grant away the payoff conflict
   entirely and value the contract at the book's own de-vigged probability —
   `V = p`, the identification that needs the fewest missing inputs. That is
   exactly the number the lane already computes. In run 66, **104 of 145
   candidates carried a probability and all 104 had `p − ask − fee ≤ 0`.**
   The other 41 had no probability at all. **Zero rows positive.** §4.

So measuring the missing settlement inputs would make a future valuation
*correct*; it would not make any of these candidates *admissible*. The
binding constraint on producing an autonomous entry today is **price**, not
settlement semantics. §5 gives the arithmetic that proves it, and §6 the
plan that follows from it.

---

## 1 · The payout events, defined before anything is valued

"Has action" and "settles to the winner" are not the same event, and the
whole analysis turns on that. Both sides below are read from captured
sources, not paraphrased.

### 1a · The venue contract (PMUS, MLB money line)

Read live from the listing's `description` field through the same
`bettor_settlement_terms.read_terms` production reads. Measured on three
distinct slugs on 2026-09-25, 379–381 characters, 4 sentences, the same
template on all of them:

> This market will settle to the winner of the *away* vs *home* MLB game
> scheduled for *date* at *time* ET. Extra innings are included if played.
> If the game is delayed, postponed, or suspended and not rescheduled to a
> date within two weeks of the originally scheduled date, the market will
> settle to the last fair market price. Outcome sourced from MLB.

`read_terms` states **exactly one** of the seven applicable conditions:

```
STATED     POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED
           → PAYS_THE_LAST_FAIR_MARKET_PRICE_OF_THE_CONTRACT_NOT_A_STAKE_RETURN
NOT STATED COMPLETED_IN_REGULATION, DECIDED_AFTER_REGULATION,
           CALLED_AND_GRADED_WITHOUT_RESUMPTION_AFTER_THE_MINIMUM,
           STOPPED_BEFORE_THE_MINIMUM,
           SUSPENDED_AND_RESUMED_WITHIN_THE_PUBLISHED_WINDOW,
           SUSPENDED_TO_RESUME_BEYOND_THE_PUBLISHED_WINDOW
```

So the venue's payoff partition is **three** classes, not two:

| class | when | payoff |
|---|---|---|
| **S** | the venue grades the fixture to a winner | `1{T wins}` |
| **B** | delayed / postponed / suspended **and** no make-up date within two weeks of the original date | `L`, the contract's last fair market price, `L ∈ [0,1]` |
| **U** | any outcome class the prose leaves silent — six of seven conditions | **unstated** |

`U` is not empty and it is not `0`, `1`, or `L`. A game called after five
innings with a leader falls in `U`: the venue publishes no payout for it.
Silence is not agreement, and it is not a zero either.

### 1b · The bookmaker (Pinnacle, baseball h2h, pre-game)

Captured from the published rules page 2026-09-24T20:30:22Z, with the quote
carried on every condition (`bettor_settlement_terms.BOOK_TERMS`). The book
pays on five conditions and **returns the stake** on two:

| condition | book payout |
|---|---|
| COMPLETED_IN_REGULATION | winner on the final score |
| DECIDED_AFTER_REGULATION | winner on the final score |
| CALLED_AND_GRADED_…_AFTER_THE_MINIMUM | winner at the last completed inning (except a bottom-half home lead) |
| SUSPENDED_AND_RESUMED_WITHIN_THE_WINDOW | winner on the final score |
| SUSPENDED_TO_RESUME_BEYOND_THE_WINDOW | winner at the last completed period |
| **STOPPED_BEFORE_THE_MINIMUM** | **stake returned** |
| **POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED** | **stake returned** |

Define `A` = the book's own action event = the complement of those last two.
Then the only probability this repository holds is

```
p = P(T wins | A)      -- bettor_pinnacle_devig, de-vigged from the book's price
```

and `bettor_pinnacle_devig.PROBABILITY_IS_CONDITIONAL_ON` already says so:
`THE_BOOKMAKERS_BET_HAVING_ACTION_UNDER_ITS_OWN_PUBLISHED_RULE`.

### 1c · The two rules are not the same variable

* The book's trigger for the void branch: **"if a fixture isn't started 12
  hours after its scheduled starting time"** — hours from the scheduled
  start.
* The venue's trigger for the price branch: **"not rescheduled to a date
  within two weeks of the originally scheduled date"** — the existence of a
  make-up date.

These are different variables. A game first pitched 13 hours late is void
for the book and graded by the venue. A game postponed and made up four days
later is void for the book and graded by the venue. The intersection is
non-empty, but it has to be *established* per fixture, not assumed. This is
the third MISSING row in
`bettor_pinnacle_devig.PRICING_A_PRICE_SETTLED_CONTRACT_REQUIRES`.

**The one measured conflict.** On `POSTPONED_OR_ABANDONED_AND_NEVER_
COMPLETED` the book returns the stake and the venue pays the last fair
market price. `PAY_LAST_FAIR_MARKET_PRICE` is never equivalent to
`PAY_STAKE_BACK` under any condition, so the comparison returns
**INCOMPATIBLE** — and did, on 14 rows across 8 distinct slugs in run 66,
with `conflicting conditions {"POSTPONED_OR_ABANDONED_AND_NEVER_
COMPLETED": 14}`. That verdict stands. It is an established conflict, not a
gap.

---

## 2 · Is there a compatible market/source pair in the supported catalogue?

**No — among the pairs actually examined.** Enumerated from the modules, not
from memory:

```
bettor_pinnacle_devig.SUPPORTED        ('baseball','h2h'), ('soccer','h2h')
bettor_settlement_terms.BOOK_TERMS     ('baseball','h2h','PRE_GAME')
                                       ('baseball','h2h','IN_PLAY')
CAPTURED_SCOPE                         ('baseball','h2h'): REGULAR_SEASON,
                                                           STANDARD_NINE_INNING
APPLICABLE_CONDITIONS                  baseball/h2h 7, soccer/h2h 5
ext_pinnacle_loop.VENUE_SPORT_LABELS   {'soccer': ('Soccer',),
                                        'baseball': ('MLB',)}
```

* **baseball / h2h** — both sides present. Verdict **INCOMPATIBLE** on the
  price-settled branch (§1c). Measured, not inferred.
* **soccer / h2h** — the source supports it and the venue lists it, but
  `BOOK_TERMS` has **no soccer capture at all**, so `book_terms()` withholds
  our own side and the comparison can only return UNKNOWN. This pair fails on
  a gap in our own capture, which is a capture task, not a conflict.

**Markets actually examined vs the wider universe.** Examined in the run-66
window: MLB money lines only — **10 distinct slugs**, of which 8 carried
complete evidence and all 8 were INCOMPATIBLE. Everything else PMUS lists —
other sports, and every market type that is not a two-way money line — is
**unexamined**. I am not claiming it contains no compatible pair; I am
claiming nobody has read it. That distinction matters for §6, because
reading it is cheap and needs no model.

---

## 3 · The rigorous bound, using only evidenced assumptions

With the partition of §1a,

```
V = P(S)·q + P(B)·E[L | B] + P(U)·E[payoff | U]          q = P(T wins | S)
```

What is evidenced, per candidate, at decision time:

| quantity | state |
|---|---|
| `ask`, `fee` | **measured** (the venue book, the venue fee schedule) |
| `p = P(T wins | A)` | **measured** (de-vigged book price, ≤ 30 s old) |
| `L ∈ [0,1]` | **evidenced**, from the contract's own range |
| `P(S)`, `P(B)`, `P(U)` | **missing** — no feed here reports postponements or make-up scheduling |
| `q = P(T wins | S)` | **missing** — `A ≠ S`, and nothing maps one to the other |
| `E[L | B]` | **missing** — a property of the venue's order book at an unknown future instant |
| `E[payoff | U]` | **missing** — the venue publishes no rule for those six conditions |

Every unknown is constrained only to `[0,1]`, and they enter `V` as a convex
combination. Therefore the tightest bound the evidence supports is

```
V ∈ [0, 1]        net edge  V − ask − fee ∈ [−(ask+fee), 1−(ask+fee)]
```

For every ask observed (all below 1), that interval **straddles zero**.
**The bound cannot establish positive net edge. Reported explicitly, as
required.**

Three tightenings were considered and all three are refused on evidence:

* **`P(B) = P(U) = 0`** ("assume it gets played and graded") — this is the
  abandonment probability itself. Inventing it is exactly what is forbidden.
  It is nonetheless carried forward in §4 as the *most favourable* case, to
  show it does not help.
* **`E[L|B] ≈ p`** ("the last price will be near the probability") — the
  venue prose makes no reference to any probability, and no venue price
  history is persisted anywhere in this repository. Assuming it would be
  inventing a last-price expectation.
* **`V ≥ P(S)·q` via `L ≥ 0`** — true, but it needs `P(S)`, which needs
  `P(B)` and `P(U)`. Unavailable.

---

## 4 · The most favourable case is already measured, and it is negative

Take the tightening anyway: set `P(B) = P(U) = 0` and identify `q = p`. Then
`V = p` — the only valuation the connected source can even *name*. (It is
not an upper bound: `L ≤ 1` means a large `E[L|B]` could in principle lift
`V` above `p`. It is the most favourable *identification*, not a ceiling.)

Under that identification the admissibility test is exactly the number the
entry lane already computes — `bettor_external_shadow`, one line, with both
sides of the subtraction describing the same payout event:

```python
rec["estimated_edge_per_contract"] = float(_p_pay) - float(ask) - fee_per
```

Run 66, SHA `905c05e`, 6 h window, verbatim from the job log:

```
in_window 145 evaluated  0 admissible  145 refused   reconciles true
  4_SETTLEMENT_SCOPE    83
  1_PROBABILITY         41
  5_EXECUTION_ESTIMATE  21
reached_execution_estimate 0   walk_took_levels 0
negative_edge_WITH_a_walk     0    (measured against walked depth)
negative_edge_WITHOUT_a_walk  104  (top of book only)
never_priced_at_all           124  (refused before 5_EXECUTION_ESTIMATE)
```

`104 = 83 + 21`: the 104 are precisely the candidates that reached a
probability, and the 41 at `1_PROBABILITY` are precisely those that did not
and therefore have no edge to report. So:

> **Of the 104 candidates that carried a probability, every single one had
> `p − ask − fee ≤ 0`. None was positive.**

**Does "top of book only" weaken this?** Not for a long entry. The lane buys
at the ask; the best ask is the *cheapest* price available, and walking
deeper can only raise the volume-weighted cost. So a non-positive edge at
the top of the book is sufficient to rule out a positive edge at **any**
size. The caution recorded on
`bettor_external_shadow.stage_report` — that an unwalked book says nothing
about depth — applies to the converse: a *positive* top-of-book edge would
not survive to a claim about capacity. That converse is not being used here.

---

## 5 · Therefore measuring the settlement inputs cannot rescue these contracts

Write the admissibility test conservatively, with the worst case `E[L|B] = 0`
(which can only lower `V`, so it cannot manufacture edge) and `P(U) = 0`
(so `P(S) = 1 − P(B)`):

```
(1 − P(B))·q  >  ask + fee
  ⟺   P(B)  <  1 − (ask + fee)/q
```

Identify `q = p`, the most favourable available value. §4 measured
`p − ask − fee ≤ 0`, i.e. `(ask + fee)/p ≥ 1`, on all 104 priced rows.
Hence the right-hand side is `≤ 0`, and

> **the threshold is unsatisfiable for every `P(B) ≥ 0`, on every candidate
> measured in the window.**

This is the sharp result. A measured `P(B)` — however carefully estimated,
however tight its interval — cannot make any of these 104 candidates
admissible. The quantity that would have to change is `ask + fee` relative
to `p`. Settlement evidence is needed for *correctness*; it is not the
binding constraint on *activity*.

Two things this does **not** say. It does not say the entry lane should stop
requiring settlement compatibility — the conflict is real and the refusal
stays. And it does not generalise past the window: 145 candidates over 6
hours on one venue and one market type is a measurement, not a law.

---

## 6 · What is actually missing, and the plan

Split by what each requirement buys.

### 6a · To value a price-settled contract at all (needed for correctness)

**R1 — `P(B)`, with an interval.** `P(delayed/postponed/suspended AND no
make-up date within 14 days of the original date)`.

*Source, already connected:* the MLB Stats API schedule endpoint this
repository already reads
(`bettor_fixture_metadata.SOURCE_URL`,
`https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=%s`). The same
endpoint accepts `startDate`/`endDate`, reports `status.detailedState`
(including `Postponed`), and carries `rescheduleDate` / `rescheduledFrom` /
`resumedFrom` links.

*Plan:* one historical sweep over completed seasons, counting per season —
scheduled games (denominator); games reaching `Postponed` (numerator 1); and
of those, whether a make-up date exists and falls within 14 days of the
original `officialDate` (numerator 2). Land it as
`bettor_postponement_base_rate.py`: a pure estimator that carries its
numerator and denominator on every estimate, returns a Wilson interval, and
**refuses** when the season sample is absent rather than falling back to a
point value. The gate consumes the interval's **upper** bound, which is the
conservative side for value.

**R2 — a bound on `E[L|B]`.** This one has **no source at all today** and
cannot be modelled from sports outcomes: it is the state of the venue's own
order book at an unknown future instant.

*Plan:* persist the venue's own mid / last-traded series per condition from
the cycle the loop already runs (`ext_pinnacle_loop.CYCLE_S = 900 s`, so
~96 observations/day/condition, no extra venue requests beyond the book read
already performed). Then, and only once real price-settlement events exist,
measure realised `L` against the pre-event mid. Until a sample of such
events exists, the only defensible treatment is the worst case `L = 0` —
which is what R1's conservative form in §5 already uses, so R2 is not
blocking R1.

**R3 — the window-alignment variable** (§1c: do the book's 12-hour trigger
and the venue's two-week trigger both fire on this fixture?). Falls out of
R1's sweep as a by-product: actual vs scheduled first pitch gives the
12-hour test, `rescheduleDate` gives the 14-day test. No separate source.

### 6b · To produce an admissible autonomous entry (the binding constraint)

§5 says R1+R2 will not do it. What could:

**R4 — a market with no price-settled branch, read rather than modelled.**
`bettor_pinnacle_devig.WAYS_FORWARD` already names it:
`A_MARKET_OR_VENUE_WHOSE_PAYOFF_IS_SCORE_ONLY`. Concretely: enumerate the
PMUS market types beyond MLB money lines, pass each description through the
existing `read_terms`, and keep the ones whose seven-condition table has no
`PAY_LAST_FAIR_MARKET_PRICE` row. This is the **cheapest** route — a read,
not a model — and it is the only one of the four that can change the sign of
`p − ask − fee`, because it changes which contracts are in the candidate set
at all. It is also the one route §2 shows is genuinely unexamined.

**R5 — a reference source whose own postponement rule matches the venue's**
(`A_REFERENCE_SOURCE_WHOSE_OWN_RULE_MATCHES_THE_VENUES`). A book that
settles a never-completed fixture to a price rather than returning the stake.
Neither supported pair does. This is source acquisition, not modelling, and
it is speculative: such a rule may simply not exist at a sharp book.

### 6c · The acceptance threshold, stated in advance

An entry is admissible only when

```
(1 − P̄(B))·p  −  ask  −  fee  >  0            P̄(B) = the interval's upper bound
```

On the 104 candidates measured in run 66 this is unsatisfiable even at
`P̄(B) = 0`. That is the number to re-measure after R4 changes the candidate
set — not after R1 lands.

### 6d · Order of work, and what it is worth

1. **R4** (a read; can change admissibility) — do this first.
2. **R1 + R3** (one sweep; needed before any price-settled contract may be
   valued, whatever its price).
3. **R2** (persist the series now so the sample exists later; it cannot be
   back-filled).
4. **R5** only if R4 finds nothing.

---

## 7 · What stays unchanged

* Funded trading and funded submission stay **disabled**; nothing here
  submits an order to a venue.
* The accounting-uncertain account stays **paused**.
* The **INCOMPATIBLE** verdict on the price-settled branch stands. Nothing in
  this document relaxes a settlement requirement, a freshness rule
  (`PINNACLE_MAX_AGE_S = 30.0`, untouched) or a risk bound to manufacture
  activity.
* The Pinnacle de-vigged probability is **not** relabelled as an internal or
  validated settlement model. It is what it says it is: `P(T wins | A)` under
  the bookmaker's own action rule.
* The two acceptance positions keep their **synthetic, modelled, unfunded**
  provenance and are not reseeded, and neither is counted as autonomous
  research output. The autonomous research lane holds **0 positions** and an
  empty P&L.
