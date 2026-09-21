# BETTOR ENGINE — the actual gap, and the decision core

Delivery 1 of the engine mandate. Restrictions unchanged: `mirror_live=false`,
no real orders, no capital authorization, production remains `3349219`,
nothing deployed. Nothing in this delivery can submit an order — asserted by
test.

---

## 1. THE ACTUAL GAP, BY INSPECTION

### 1.1 Integration map (runnable: AST scan of `sportsassets/`, cross-checked
against `workers/`, `api/`, `scripts/` and `tests/`)

13,590 lines of `bettor_*` modules exist. The components are largely written.
**They are not connected.**

| module | non-test callers | in workers/api |
|---|---|---|
| **`bettor_fair_value`** | **0** | **0** |
| **`bettor_replay_harness`** | **0** (not even a test) | 0 |
| **`bettor_preregistration`** | 0 | 0 |
| `bettor_rehearsal` (imports 11, the nearest thing to an orchestrator) | 1 | 0 |
| `bettor_ev_bridge` | 10 | 1 |
| `bettor_inventory` | 8 | 1 |

The fair-value module — the heart of an EV engine — has **zero non-test
callers**. The would-be orchestrator is a leaf nothing invokes.

### 1.2 Every terminal quantity is `NOT_IDENTIFIED` (runnable: executed)

```
ev_bridge.evaluate({yes_bid:.44, yes_ask:.47, no_bid:.52, no_ask:.55})
    -> bestAction NO_TRADE, actionEvStatus NOT_IDENTIFIED
fair_value.fair_value()      -> FV_BETTOR_INDEPENDENT: NOT_IDENTIFIED
fair_value.directional_permitted() -> permitted: false
p_fill.p_fill()              -> P_FILL: NOT_IDENTIFIED
merge.merge_permitted(...)   -> false for BOTH retail and institutional
shadow_execution.hypothetical_quote() -> TypeError (5 required kwargs)
```

**What exists is a framework of refusals** — a system that knows what it may
not claim. That is genuinely valuable and it is why nothing bad has been
traded. It is not a decision engine: before this delivery, **no module took a
market observation and returned an action, a size and a reason.**

### 1.3 Component status

| capability | status | evidence |
|---|---|---|
| Fair value (venue-implied) | **implemented, now integrated** | `bettor_fair_value.py`; wired by `bettor_decision_engine.py` |
| Fair value (independent) | **measured and rejected** | blend scored **worse** than market by 0.00926 log loss, 95% event-clustered CI [-0.00222, +0.02036], `NOT_DETECTED_AT_THIS_SAMPLE_SIZE` |
| Action comparison | **was missing → built** | `bettor_decision_engine.decide()`, 35 tests |
| Execution modelling | **partial** | `bettor_shadow_execution` exists; `P_FILL` unidentified so maker actions unscorable |
| Inventory | implemented, integrated | `bettor_inventory.py`, 8 callers |
| Sizing | **was missing → built (depth-bounded)** | `decide()` sizes at `min(both depths, max_contracts)` |
| Accounting | partial | `shadow_bettor_accounting.py` orphaned (0 callers) |
| Risk controls | implemented, partially integrated | `bettor_risk_engine.py`, 3 callers |
| Autonomous orchestration | **missing** | no loop exists; `bettor_rehearsal` is a leaf |

---

## 2. THE DECISION CORE — `backend/sportsassets/bettor_decision_engine.py`

One market state in, one auditable decision record out. 35 tests.

### 2.1 What it is allowed to decide, and on what authority

It does **not** invent an edge. Directional action is `BLOCKED` — not merely
unscored — because `P_BETTOR_INDEPENDENT_V3` measured the challenger as
**worse** than the venue price on held-out events. An engine that opened
directional positions anyway would be overriding its own evidence.

| action | status | why |
|---|---|---|
| `PAIR_BUY` / `PAIR_SELL` | **IDENTIFIED** | YES+NO pays exactly 1.00 at settlement whatever happens, so `EV = 1.00 − yes_ask − no_ask − fees` contains **no forecast** |
| `HOLD` / `NO_TRADE` | IDENTIFIED (baselines) | 0 incremental cash by construction |
| `TAKE_*` / `SELL_*` | **BLOCKED** | fair value measured and rejected |
| `MAKE_YES` / `MAKE_NO` | **NOT_IDENTIFIED** | requires `P_FILL`; **NOT_IDENTIFIED is not zero** and never wins by default |
| `MERGE` | **UNSUPPORTED** | no merge mechanism observed on either venue; a mechanism available to a reference account is not thereby available to the institutional account |

### 2.2 One reconciled cash-flow model

Every action's value is the sum of the dated cash flows it causes, and nothing
else. Spread capture, fair-value gain, pairing benefit and markout are **not
separate addends** — they are descriptions of the same cash. A pair buy is
`−yes_ask −no_ask −fees` now and `+1.00` at settlement. Asserted:
`ev_net == cash_now + cash_at_settlement`, exactly.

Unverified incentives are zero. `Fees().rebate_verified_per_contract == 0.0`,
and a test confirms an unverified rebate cannot rescue a losing trade.

### 2.3 Demonstrated behaviour

```
normal market (asks .47/.56)   -> NO_TRADE   PAIR_BUY scores -15.00
asks sum to 0.97               -> PAIR_BUY   size 300, ev +9.00
   cash_now -291.00, settlement +300.00      (sums exactly to ev)
same with 2c/contract fees     -> NO_TRADE   ev falls to -3.00
0.97 but zero depth            -> NO_TRADE   NO_EXECUTABLE_DEPTH
stale book (45s)               -> NO_TRADE   data_quality REJECTED
unparsable quotes              -> NO_TRADE   ASK_UNREADABLE
held pair, bids sum 1.03       -> PAIR_SELL  size 200, ev +6.00
40-cent spread, maker unscored -> NO_TRADE   P_FILL_NOT_IDENTIFIED
```

The 40-cent-spread case is the one that matters most: a large notional prize
is on the table and the engine declines, because the probability of collecting
it is unmeasured. **An unscored action does not become attractive by being
unmeasured.**

Sunk cost never enters: two identical books with bases of 0.01 and 0.99
produce the same decision and the same EV.

---

## 3. THE BLOCKING DEFECT — the complement quote is never captured

This is the most important finding in the delivery, and it is a **data
defect, not an economic result.**

### 3.1 Census of 1,392 observations (7 days)

```
both_asks_readable      0        <-- zero
yes_ask parses          820
no_ask parses           0
an_ask_is_null          0
```

### 3.2 What `no_ask` actually contains

```
no_ask_value      rows
NOT_IDENTIFIED    1393      <-- the literal sentinel string, every row
```

`yes_ask` is `NOT_IDENTIFIED` on 573 rows and a real price on 820. **`no_ask`
and `no_bid` are the sentinel on 100% of rows.** The collector writes one
leg's quote into the `yes_*` columns and never populates the complement.

And `outcome_leg` shows one row is **one outcome leg, not one market**: 1,045
rows are `'no'`, 158 `'over'`, 8 `'yes'`, and the remainder are player or team
names (`filip misolic`, `atlanta braves`, …).

### 3.3 What this means

**The engine's only identified-EV action cannot be evaluated on a single
captured row.** The pair sum needs two prices at the same instant; the dataset
has one.

It would be wrong to report "no arbitrage exists" from this. What exists is a
dataset that cannot answer the question. The correct statement is:

> **Model-free pair EV is unmeasured on captured data, because the complement
> quote has never been captured.** Neither its presence nor its absence is
> established.

### 3.4 Supporting measurements that did land

| | |
|---|---|
| Spread (yes leg), p10 / p50 / p90 | 0.0100 / **0.0300** / 0.8900 |
| Spread ≥ 10c | 278 of 695 |
| Non-positive spread | 0 |
| Distinct markets/day | 810 (09-21), 570 (09-20) |
| Distinct events/day | 367 (09-21), 139 (09-20) |

A p90 spread of 0.89 means a large fraction of observed markets have
effectively no two-sided book. That is a capacity fact and it belongs in §5 of
the final report.

---

## 4. HONEST STATUS AGAINST THE MANDATE

| mandate item | status |
|---|---|
| 1. Establish the engine gap | **complete**, evidence above |
| 2. Economically explicit decisions | **core complete**; latency, partial fills and conditional fill probability still unmodelled (blocked on `P_FILL`) |
| 3. One complete autonomous shadow loop | **not started** — next |
| 4. Establish a probable edge | **not established.** Directional is measured-and-rejected; maker is unmeasurable without `P_FILL`; pair EV is unmeasurable until §3 is fixed |
| 5. $500k/day capacity | **not established.** Cannot be, until §3 gives an eligible-opportunity count |
| 6. Institutional operating controls | gate + admission done (previous delivery); reconciliation reads built; limits/restart/monitoring outstanding |

### 4.1 The critical-path forecast

The order is forced by dependency, not preference:

1. **Fix complement capture** — the collector must record both legs of a
   market at one instant, or the pair sum must be reconstructed by joining two
   observation rows of the same event and bucket. This is the gate on items
   4 and 5. *Next deliverable.*
2. **Shadow loop** (item 3) — buildable now against synthetic and replayed
   books; it demonstrates software behaviour and cannot establish
   profitability.
3. **Edge evidence** (item 4) — requires (1), then a pre-registered holdout.
   Candidate policies, economic assumptions, benchmarks and acceptance rules
   must be frozen **before** the holdout is read.
4. **Capacity** (item 5) — requires (1) for eligible-opportunity counts.

**I will not have a defensible positive-edge claim without (1), and (1) needs
fresh collection after the fix — the existing 1,393 rows cannot be
retrofitted.** That is the binding constraint on the deadline, and I am
stating it now rather than at the end.

### 4.2 What I will not do

- Revive cheap-first-leg entry or the failed directional candidates. Track P
  stays a preserved negative result.
- Weaken an acceptance rule to produce a positive finding.
- Report synthetic-scenario success as evidence of profitability.
- Treat a price touch as a confirmed fill.

---

## 5. ARTIFACTS

| | |
|---|---|
| Decision core | `backend/sportsassets/bettor_decision_engine.py` |
| Tests (35) | `backend/tests/test_bettor_decision_engine.py` |
| Model-free EV census | `research/bettor_model_free_ev_census.sql` · run [35626484955](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35626484955) |
| Complement-quote diagnosis | `research/bettor_no_leg_quotes_missing.sql` · run [35626793729](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35626793729) |
