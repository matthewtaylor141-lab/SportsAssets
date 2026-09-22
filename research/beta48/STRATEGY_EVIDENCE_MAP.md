# BETTOR strategy evidence map

Every material internal study, read for one question: **what does it
license us to believe about money BETTOR could make on PMUS?**

Two distinctions run through the whole table and are never collapsed:

- **Observed account behaviour** vs **inferred rule.** We see fills. We
  do not see intent, and a rule reconstructed from fills is a hypothesis
  about the account, not a description of it.
- **Venue.** RN1, Ferrari and the other four traded the **Polymarket
  global CLOB**. BETTOR trades **Polymarket US**. Different fee
  schedule, different book, different participants. A figure from one
  is not a figure about the other, and the only verified fee schedule we
  hold is PMUS's.

---

## 1. The accounts

### RN1 — the only account whose raw fills we hold

| | |
|---|---|
| **Proposed source of profit** | matched-pair economics: buy one leg, buy the complement later, collect the $1 settlement for less than $1 |
| **Dataset** | `research/snapshots/u2_events_v1.jsonl.gz`, 214,609 fills, 214,609 distinct `trade_id`, 17,752 conditions |
| **Period** | 2026-08-06T04:36:32Z .. 2026-09-11T23:59:34Z (37 days) |
| **Venue** | Polymarket **global CLOB**, not PMUS |
| **Account mechanics** | **BUY-only.** Every one of the 214,609 rows is `side = BUY`; he exits by buying the complement, never by selling |
| **What it measures** | **settlement payoff**, reconstructed offline by `merge_pnl.replay` |
| **Costs included** | the fills' own prices |
| **Costs omitted** | **the CLOB fee schedule entirely** — it is not the verified PMUS one and was never read back; no rebates; no slippage beyond the printed price |

**Measured:** edge_roi **+2.084%**, 95% CI **[−0.195%, +4.362%]**,
cluster-robust on his game. Monotone decay as the window narrows:
+2.08% (37d) → +2.15% (30d) → +1.21% (14d) → **+0.26% (7d)**.
`merge_pnl`'s own `edge_verdict` reads **NOT DEMONSTRATED** in every
window. `edge_coverage` is 0.742, so a quarter of deployed dollars are
ungraded, and 8.3% of fills were dropped for a missing condition id.

**Verdict: `RN1_PAIR_EDGE = NOT_IDENTIFIED` (point +2.08%, CI crosses
zero).** The directive's report-derived hypothesis of "strong positive
matched-pair economics" is **not confirmed** on his own raw data. It is
not refuted either — the interval is wide.

**What transfers:** nothing directly. His fill prices are not achievable
fills for us; the fee schedule differs; the decay says the effect, if
real, is weakening.

**What it is genuinely useful for:** RN1's flow is the **only dataset in
which a resting quote is OBSERVED to fill.** Because he only ever buys,
somebody's offer was lifted, and that somebody's economics are
computable. That makes it a **stress case**, below.

### Ferrari (ferrariChampions2026) — reachable only as an aggregate

| | |
|---|---|
| **Proposed source of profit** | the report's pair-POSITIVE exemplar |
| **Raw fills** | **ABSENT.** `RAW_FERRARI` is not on disk, and `whale-full-history.yml`'s matrix is `rn1, w2c33, homerunhazard, swisstony, kch123` — **it does not cover Ferrari** |
| **What we do hold** | aggregates only: MERGE **+$10,804,770**, SETTLED **−$8,417,042**, 57.7% of stake held to settlement |
| **Reconciliation** | pair channel **CONSISTENT** with the report (1.14 pp); directional channel **SIGN_AGREES_MAGNITUDE_DIFFERS** (3.68 pp) |
| **What it measures** | settlement payoff at the account level, not per-fill |
| **Costs omitted** | same as RN1, plus we cannot inspect the fills at all |

**Verdict: `FERRARI_MECHANISM = NOT_IDENTIFIED`.** We can confirm the
sign of two channels and nothing about *how*. Any statement about
Ferrari's entry rule, sizing or timing is an inference from a total.

**This is a named missing interface fact, and it is cheap to close:**
add `ferrarichampions2026` (`0xfe787d2da716d60e8acff57fb87eb13cd4d10319`)
to the extraction workflow's matrix. That is one line; it needs a
decision, not research. Until then the four-account comparison the
directive asks for is **NOT_IDENTIFIED for 3 of 4 accounts** (Ferrari,
HomeRunHazard, swisstony all lack raw fills locally).

### The cross-account result that governs all of them

The pair channel — the **only** mechanism a copy strategy would
reproduce — **changes sign across the six accounts**:

| account | MERGE (pair) | SETTLED (hold) | held to settlement |
|---|---|---|---|
| ferrarichampions2026 | **+$10,804,770** | −$8,417,042 | 57.7% |
| rn1 | **+$10,364,423** | +$2,935,868 | 43.9% |
| swisstony | **+$260,123** *(disputed sign)* | +$23,357,748 | 68.0% |
| homerunhazard | −$476,349 | +$2,961,211 | 61.7% |
| kch123 | −$673,482 | +$10,499,607 | 91.9% |
| w2c33 | −$7,880,573 | +$15,843,715 | 80.3% |

**Three positive, three negative.** And between 43.9% and 91.9% of the
capital behind every headline figure is **held to settlement, not traded
as pairs** — so the headline `edge_roi` is mostly not the pair channel
at all.

`FOUR_ACCOUNT_PAIR_COMPARISON_VALID = NO` (swisstony's pair channel
sign-flips against the report: −$3.39M reported vs +$260k measured, a
near-zero quantity against a $249M stake — which is precisely the
quantity a pair strategy would be built on).

**Consequence for the mandate: "copy the profitable whale" is not a
supported hypothesis.** There is no stable pair-channel sign across
accounts to copy, and the one account we can inspect in full does not
demonstrate its own edge.

---

## 2. The strategy classes, and where each one actually stands

| class | mechanism | status | what decided it |
|---|---|---|---|
| **A** | passive same-venue complementary maker pair | **NOT_IDENTIFIED** | the binding term is unmeasured |
| **B** | maker first leg + controlled completion | **NOT_IDENTIFIED** | same term |
| **C** | taker complementary pair | **FALSIFIED** | same-venue arithmetic, re-run this session on a fresh holdout |
| **D** | Phase X cross-venue PMUS/Kalshi | **FALSIFIED, LOCKED** | its own holdout, −11.1% at zero fees |
| **E** | directional taker on independent fair value | **NOT_COMPARABLE** | no edge estimate exists to rank |

### Class C — re-tested this session, on data never opened before

`research/beta48/bettor_economic_test.py`, 788 two-sided OPEN
observations across 8 markets, pulled from the raw capture segments
(the curated 400-pair file is development data and is reported
separately):

```
median basis  ask + (1 - bid)      1.0050
minimum basis observed             1.0050
pairs BELOW PAR before fees               0
pairs PROFITABLE after PMUS fees          0
median EV per contract             -0.0325
VERDICT                            FALSIFIED
```

`ask + (1 − bid) = 1 + spread` is an **identity**. The pair costs par
plus the spread before either taker fee is charged. This class needs no
fill model and no fair value, which is why it is the only one that can
be settled on recorded data — and it settles negative.

### Class D — Track P, preserved and not to be retuned

`TRACK_P_GATE = P-C`. ROI +10.5% TRAIN → +0.5% VALIDATION →
**−11.1% HOLDOUT**, 95% CI [−15.5%, −6.6%], 1,170 independent markets,
**negative at zero fees** so no fee correction revives it. A locked
negative result.

### Classes A and B — what is missing, named precisely

Four objects, kept apart (never substitute one for another):

| | estimand | status |
|---|---|---|
| **A** | `E[SETTLEMENT − QUOTE \| STATE]` | **NOT YET MEASURED.** Measurable prospectively without P_FILL, but only given all three of: matched authoritative settlements, adequate coverage, and a valid clustered evaluation. Observation supplies the QUOTE and STATE sides and **none** of those three |
| **B** | `P(FILL \| STATE, QUOTE)` | **NOT_IDENTIFIED** |
| **C** | `E[SETTLEMENT − QUOTE \| FILLED, STATE]` | **NOT_IDENTIFIED** |
| **D** | maker EV = f(B, C, fees, rebates, inventory) | **NOT_IDENTIFIED** |

**The binding unknown is object C.** What is measured on PMUS is the
gross half-spread. What decides the sign is what a resting order earns
against the flow that actually meets it, and nothing we hold identifies
it:

- On disk it cannot be measured at all. The PMUS book/outcome join is
  **literally zero rows** (30 slugs with a two-sided book, 10,257 with
  an outcome, intersection 0). `DATA_GATE = DATA-B`.
- The only depth+settlement dataset is ask-side only, on a different
  venue, and selected because RN1 traded it.

### The stress bound, and the scope it must keep

Resting an offer that RN1 lifts earns **−0.0090/share**, 95% CI
[−0.0143, −0.0038], n = 9,337 independent conditions, settlement strictly
after trade. The verified PMUS maker rebate at the holdout's median mid
covers **32.2%** of that deficit.

**This is `TOXIC_FLOW_STRESS_CASE`, not an expectation.** It says what a
maker earns when the counterparty is a specifically informed trader
lifting at the touch. `SELECTION_BIAS = SEVERE`. Subtracting a
global-CLOB adverse-selection number from a PMUS half-spread produced a
headline this programme has already had to retract once; it is not
repeated here.

---

## 3. What this session added to the map

1. **Class C re-falsified on a genuine holdout** (788 obs, 8 markets,
   never opened before the policies were frozen), with PMUS taker fees
   charged on both legs by the same accounting model that prices every
   other action.
2. **A tick finding.** 493 of 788 holdout observations are quoted on a
   **half-cent grid** (0.600 / 0.605), not a cent. A policy assuming a
   1c tick quotes **through the touch** on 63% of this cohort. The venue
   carries `market.orderPriceMinTickSize` per market; the accounting
   model now reads it.
3. **A capacity verdict against $500,000/day** built from measured
   volume and measured queue depth rather than an assumed fill rate —
   including the correction that at the measured 33-hour queue time the
   working capital is **$688,179**, not the **$41,667** a two-hour
   holding assumption gives.
4. **Ferrari named as a one-line data gap**, not a research problem.

## 4. What would actually move the maker classes

Exactly one thing identifies object C: **BETTOR's own admitted fills.**
Everything else — including a perfect prospective capture of book and
settlement on an unselected PMUS universe — identifies object **A** and
leaves fill-selection bias exactly where it was.

That is why the observation probe is worth running and why it is not
sufficient on its own: it builds the clean state frame and measures
which states later move adversely. It does **not** measure what our
resting order would have earned, because it never rests one.

The honest ordering is:

1. **Observation probe** (authorised, bounded, decision-only) — gives
   object A, real venue behaviour, real frame semantics, and the first
   PMUS WebSocket evidence of any kind.
2. **A tightly bounded maker pilot** — the only thing that gives object
   C. It requires an order path and capital, neither of which is
   authorised, and it should not be proposed until step 1 has produced
   the state frame that would size it.

No step between them produces a maker EV, and no rebate, fee correction
or another account's P&L substitutes for one.
