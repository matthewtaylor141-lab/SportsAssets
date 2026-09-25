# The external-valuation entry lane: operating record

What this release built, what it is allowed to do, what it actually did in
production, and the one thing that still stops it creating inventory.

**Funded submission is disabled. The accounting-uncertain account remains
paused. No order was submitted to any venue by anything described here,
and no capital is at risk.**

---

## 1 · Deployed identity

| | |
|---|---|
| Release branch | `claude/command-center` (not auto-deployed) |
| Backend SHA under test | filled in below, per run |
| Route | image gate → `render-ops deploy-api-commit` by SHA, API only |
| Worker | untouched; `claude/session-njaewf` was **not** pushed, because that is the branch the protected worker auto-deploys from |
| Migrations | applied at API boot by `start.sh` → `python -m sportsassets.scripts.migrate` |

---

## 2 · What the lane is

```
supported market  →  Pinnacle de-vig (EXTERNAL, unvalidated)
                  →  settlement comparison, scoped by authoritative fixture metadata
                  →  marketable execution estimate against the observed ladder
                  →  size from the frozen $1,000 notional policy
                  →  risk engine, with this lane's predeclared limits
                  →  entry gate
                  →  rn1x_positions / rn1x_decisions / rn1x_orders / rn1x_fills
```

Every stage is an engine that already existed. This release supplied the
three inputs that were placeholders and connected the output to the
ledger.

### What is NOT claimed

- Pinnacle's de-vigged probability is **not** an internal model and its
  accuracy has **not** been validated. `qualified_model` is `false` on
  every admitted decision and `probability_validated` is `false` in the
  decision's own input labels.
- A fill is **modelled** against depth the venue was displaying.
  Displayed depth is not guaranteed depth; `is_modelled` is true on every
  order and fill, and `execution_secured` is false on every decision.
- Clearing this lane's risk rails is **not** a capital authorization. The
  limits are shadow limits for a lane that submits nothing.

---

## 3 · The three inputs that were placeholders

| Input | Was | Is |
|---|---|---|
| execution estimate | `p_fill: None` — refused every candidate | `shadow.marketable_fill` over the observed acquisition ladder |
| size | `1.0` — a literal | `shadow_bettor_sizing`, the frozen $1,000 intended notional |
| risk | `{"permitted": True, "reason": "shadow, no capital"}` | `bettor_risk_engine.evaluate` with this lane's predeclared limits |

### Marketable execution is not a resting order's fill probability

Two engines, kept apart:

| | engine | what it answers |
|---|---|---|
| resting | `PRINT_THROUGH_WITH_QUEUE_SHARE_V1` | a **forecast** about other people's future orders, dependent on queue position. **Not used by this lane.** |
| marketable | `MARKETABLE_RECONSTRUCTED` | an **observation** of the arrival book at or inside our limit |

`p_fill` here is `EXECUTED_NOTIONAL_OVER_INTENDED_NOTIONAL` with basis
`MARKETABLE_COVERAGE_OF_OBSERVED_ARRIVAL_LADDER` and `is_forecast: false`.
It is below 1 whenever the ladder is thin or priced beyond break-even, 0
when nothing is inside it, and **absent** when the ladder is unreadable —
there is no path that reaches 1.0 for want of data. The latency gap
between the read and any arrival is **reported** (`observation_age_s`),
not discounted: a haircut invented here would be an invented estimate.

The limit price comes from the belief, not the book: `fair_value −
fee_per_contract`. Sizing to whatever the ladder held would have made
coverage 1.0 by construction and measured nothing. The price handed to the
gate is the **VWAP of the walk actually claimed**, not the best level —
pricing a multi-level size off level one understates cost and turns depth
into edge.

---

## 4 · The predeclared risk limits

Every dollar figure is the frozen standard notional times a written-down
count of trades. The count is the judgement; the dollar figure is not a
separate choice.

| rail | limit | basis |
|---|---|---|
| MAX_MARKET_EXPOSURE | $1,000 | one standard trade per market |
| MAX_EVENT_EXPOSURE | $1,000 | one standard trade per **event** |
| MAX_CAPITAL_DEPLOYED | $3,000 | three standard trades open at once |
| MAX_CORRELATED_EXPOSURE | $1,000 | one standard trade of **worst-case** correlated exposure |
| MAX_RESIDUAL_INVENTORY | 2,000 contracts | unpaired directional quantity |
| MAX_CAPITAL_HOURS | 72,000 usd-hours | one standard trade held 72 h |
| MAX_DRAWDOWN | $1,000 | realised losses plus worst-case unrealised |

Measurement conventions, each chosen so it can only refuse:

- **Correlated exposure** assumes every open position is perfectly
  correlated with the proposed one, because no correlation structure is
  known. A fitted correlation would discount exposure; this cannot.
- **Drawdown** counts an unsettled position whose mark is unavailable as a
  **total loss**. An unmarked position is not a position that is fine.
- **Capital-hours** measures hours **accrued**, which is observable. It is
  not a forecast of holding period — this lane has no exit rule, so that
  number does not exist and is not invented.
- **A settled position is excluded from the exposure rails** (its basis
  came back) but its loss counts in full on drawdown.
- **An unread book is not an empty one.** It leaves every rail
  NOT_EVALUABLE, which blocks.

The module's own rails still carry **no** numbers. These limits live with
this lane, so a lane that declares nothing is still refused.

---

## 4b · Connection defects found by review, and repaired

A review of `a95a749` found five. All were real; each is listed with what
it would have done if left.

| # | defect | what it would have done |
|---|---|---|
| 1 | `cycle` returned `IDLE_NO_CANDIDATES` **before** the management step | on every cycle where the cohort produced no new fill — the ordinary state — nothing open was re-evaluated, including positions whose exit condition had arrived |
| 1b | the manager runs per experiment, with the challenger's id | entry-created inventory sat in the shared ledger and was re-evaluated by nothing: the Ferrari failure's shape |
| 2 | order and fill ids hung off `int(now)` | a later cycle minted new ones, both inserted, while the position row hit `ON CONFLICT DO NOTHING` — executions accumulating against inventory that never grew |
| 3 | `open_book` read once per cycle | two $600 entries, each admissible against an empty book, both clear a $1,000 combined rail |
| 4a | `limit_price` carried the VWAP and the writer used it as the order's limit | a walk over .62/.64/.66 recorded as an order limited at .635556 that filled twice above it — unreconcilable against any venue |
| 4b | the outcome index was inferred from the US order intent | a catalogue whose token order differs files the position under the opposite leg and reads settlement off the wrong outcome |
| 4c | the decision instant was taken **before** the venue, rules and metadata reads | a decision claiming a freshness it did not have — the same defect already repaired in management |

And two more that only running the lifecycle could find:

| # | defect | what it did |
|---|---|---|
| 5 | `store.persist_run` did `int(trade_id)` | raised `TypeError` on every management cycle for a position with no source trade: examined every cycle, failed every cycle |
| 6 | it re-derived the position id as `experiment:policy:None` | management tried to write a **second** position row; migration 116's index refused it. The index was right; the re-derivation was the fault |

Plus one of mine: a second, uncached venue-rules reader added beside the
cached one that already existed — a parallel implementation spending a
paced request per candidate per cycle on a string that does not change.
Deleted.

### The prices, kept apart, each with exactly one consumer

| | what it is | who reads it |
|---|---|---|
| `submitted_limit` | the break-even the belief implies: `fair_value − fee`. Nothing is taken above it. | the ORDER record |
| `acquisition_cost_per_contract` (= `vwap`) | what the quantity actually cost, volume weighted. An **outcome** of the walk, never a limit. | the ECONOMIC comparison — the gate's edge |
| `fee_per_contract_realised` | the per-level fees actually charged, summed and divided by the quantity filled | the same comparison, on both sides of it |
| `worst_case_cost_per_contract` (= the limit) | deliberately the worst case: nothing fills above the limit, so reserving there reserves the most the order could consume | the EXPOSURE reservation |
| `levels_taken` | per-level price, quantity and cost, so all of the above can be checked against the book. One fill row per level, each with the fee charged at that price. | the fills, and any recheck |

The writer refuses outright if the submitted limit is below the VWAP: the
two did not then come from the same walk.

Each row records which price it used: `executable_price_basis`,
`cost_per_contract_basis`, and `exposure.proposed_cost_basis`.

### The three write cases

| case | what happens |
|---|---|
| `NEW_EXPOSURE` | written |
| `EXACT_REPLAY_OF_A_RECORDED_OBSERVATION` | nothing written, reported as a replay rather than as a write that changed nothing |
| `NEW_QUOTE_ON_AN_ALREADY_HELD_EXPOSURE` | refused. It is an ADD: it changes average cost and size, re-opens the market-exposure rail against the combined position, and needs its own basis for the second tranche |
| `ADD_TO_AN_EXISTING_POSITION` | `ADD_SUPPORTED = False` — off rather than absent, so the refusal has a name to look up |

---

## 4c · A second review, of `85469ab`, found four more

All four were real. Each is stated with what it would have done, and each
is now pinned by a test built from the review's own counterexample.

| # | defect | what it did, or would have |
|---|---|---|
| 1 | `_entry_plan` returned `ask = est["limit_price"]` — the SUBMITTED limit — and `evaluate` labelled it `VWAP_OF_THE_SIZED_WALK` | the limit is by construction `fair_value − fee`, so the gate's edge was zero less rounding and every candidate refused. **Reproduced**: probability .75, observed VWAP .625, fee .03125 → gate saw .71875, edge −0.00495; the walked cost gives +0.09. This accounts for run 48's 159 `NO_ACTION_HAS_POSITIVE_NET_EDGE` refusals as *arithmetic*, not a market fact |
| 2 | `join_outcomes` read the venue's YES settlement through `payout_is_complement` | that flag describes the PROBABILITY's source event and is `false` on this lane even when the exposure is the venue's SHORT side. A YES settling at 1, held short, was recorded as outcome 1 while the exposure paid 0 — exactly wrong on every short leg |
| 2b | `RESOLVED_DERIVED` returns a NAMED winner; `float()` on it raises | the caller recorded `None`, which was written as VOID, permanently, for a fixture that had resolved |
| 2c | a void was inferred from any nonbinary value, and recorded as `outcome_known` with a NULL outcome | migration 103's CHECK forbids that, so the write raised and was counted as an error — voids were re-read every run forever, and the comment claiming otherwise was wrong |
| 3 | the calibration evaluator admitted evidence that did not support its claim | it returned PASSED for 300 forecasts all saying .60 on fixtures that won 90% of the time (Brier .18 under a .24 ceiling, while a constant .90 scores .09; `beats_base_rate` was `false` and was ignored), and counted 150 fixtures represented by both complementary payouts as 300 "unique events" |
| 4 | the lifecycle test INSERTed into `rn1x_outcomes` and asserted what it had written | nothing in production created an outcome for an entry-lane position. `manage_open_position` returns SETTLE with `settled=None`, because the challenger's settlement input is the ranking pipeline's observed-payout dict, which this lane never populates |

### Two conversions, and they are not one flag

| conversion | question | applied |
|---|---|---|
| PROBABILITY | is the de-vig's source event the complement of the payout event? | once, in `bettor_external_shadow.evaluate`, at decision time. By the time a row is written, `probability` already describes the payout event |
| SETTLEMENT | is the exposure we hold the venue's LONG side or its SHORT side? | once, at the join and at settlement, from `buy_intent` / `ladder_side` — which must agree, or the identity is refused |

`outcome_from_settlement` is the single mapper, used by BOTH the
calibration join and the settlement consumer, so the two cannot disagree.

### Four kinds of "no outcome", kept apart

| class | written? |
|---|---|
| a settled 0/1 (`VENUE_SETTLEMENT_PRICE` / `VENUE_REPORTED_OUTCOME`) | the outcome, with its basis and the side map used |
| `CONFIRMED_VOID` — the venue's own endpoint, a parseable price, neither side | the basis only; `outcome_known` stays false, because a void has no 0/1 truth |
| `VENUE_NAMED_A_WINNER_NOT_A_PRICE` | nothing. The attempt is stamped so the read queue does not starve |
| `INFERRED_FROM_CONVERGED_PRICES_NOT_VENUE_SETTLEMENT` | nothing. The venue reported no winner; this is our inference from a price |
| `SETTLEMENT_VALUE_UNPARSEABLE`, `VENUE_SIDE_IDENTITY_NOT_ESTABLISHED` | nothing |

`outcome_basis IS NOT NULL` is one condition doing two jobs: it is the
join queue's exit condition and the calibration scope's entry condition.

### The audit of what the previous release joined

Migration 118 reopens every `external_valuations` row whose outcome was
set while the complement mapping was in force: the prior value and
timestamp are preserved verbatim in `outcome_audit`, the outcome is
cleared so the corrected mapper re-reads the venue, and the row stays out
of calibration until a basis is recorded for it. `classify` also treats
any 0/1 without a recognised basis as `UNVERIFIED_OUTCOME_PROVENANCE`, so
the exclusion does not depend on the migration having run.

---

## 4d · Settlement, by production code

`bettor_entry_settlement` is the consumer that closes an entry-lane
position. One transport boundary, everything else production code.

| step | what |
|---|---|
| read | `GET /v1/markets/{slug}/settlement` via `bettor_live_read.read_resolution` — the venue's own answer |
| map | `outcome_from_settlement`, the same venue-side identity the calibration join uses |
| account | payout, realised cash, fees, residual closed to zero, net. `NET = REALIZED_CASH − COST_BASIS_INCLUDING_FEES` |
| write | `rn1x_outcomes`, **exactly once**: `ON CONFLICT DO NOTHING`, and a settled position is no longer in the open query at all, so a second pass across a restart does not even re-read the venue |
| release exposure | by that row alone. `exposure_from_rows` excludes any position whose `realized_net_usd` is non-NULL — one fact, one place, no second write |

**It needs no bookmaker odds, and the test proves it by making the odds
fetch raise.** A finished contract's value is the venue's settlement
price. Requiring a live quote to settle a market that is already over is
the defect that left a finished fixture carried as open inventory.

**MLB "Final" settles nothing.** It establishes that a game ended. It does
not establish that this venue settled this contract, or that it settled it
under rules compatible with the captured terms. So the venue is what is
asked — for the entry lane's positions and for the **acceptance position**
alike, both swept by `run_continuing_management` on every cycle, including
cycles with no new candidates.

Nothing is reseeded. A settlement is a row *beside* the position; the
acceptance position's synthetic, modelled, unfunded provenance is neither
read nor rewritten here.

A void returns the stake and **states** its one assumption: whether the
venue refunds its fee on a voided market is not in the captured terms, so
the conservative reading is taken and named
(`FEES_ASSUMED_NOT_RETURNED_...`), not hidden.

---

## 5 · The standing blocker

`MODEL_TRUST_DRIFT` is NOT_EVALUABLE and it **blocks the creation of
inventory**.

`bettor_pinnacle_devig`'s own note on its default method is *"validate in
shadow, which is what this source is for."* Its calibration is therefore
unmeasured, and the gate reads a **row** — `external_source_calibration`
— not a constant. With no row the answer is "not measured".

This is not circular. What the gate blocks is creating a **position**. It
does not block the lane's actual shadow work: recording the valuation, the
price, the costs and the verdict on every supported market every cycle.
Those records, paired with settled outcomes, are the calibration evidence
the gate is waiting for. The lane accumulates its own key.

**The measurement now exists and runs.** `bettor_source_calibration`
declares, before any data is read:

| | |
|---|---|
| scope | source version, de-vig method, sport families, market |
| point in time | the probability **recorded** at decision time, and per fixture the **first** one — never the last, the best, or an average, which would be choosing among a source's own revisions after seeing which way the event went |
| independent unit | one observation per **FIXTURE**. A source quoted every fifteen minutes for six hours produces twenty-four rows about one coin flip, and the two sides of a two-way market are two statements about the same coin flip. Neither adds a degree of freedom |
| outcome classes | RESOLVED / VOID / UNRESOLVED / **UNVERIFIED**. Only RESOLVED is scored, and RESOLVED requires the venue's own settlement provenance on the row. All four counts are reported |
| baseline | a constant forecast fitted on the chronologically **earliest third** of resolved fixtures, which are then **excluded from scoring**. Fixed without any evaluation outcome, by construction |
| metric | BRIER — proper, so it cannot be improved by shading a probability away from the honest one — reported **with its standard error** |
| acceptance | **four** conditions, all required: ≥ 300 independent evaluation fixtures; Brier ≤ 0.24; a paired improvement over the held-out baseline whose 95% interval excludes zero; and an expected calibration error ≤ 0.05 |
| reproducible | the result carries its inputs' hash |

**What the evaluator returns that is not the verdict**, because these are
three different questions:

| | |
|---|---|
| predictive score | the Brier, with Murphy's decomposition into reliability − resolution + uncertainty |
| calibration | the binned reliability table, its expected calibration error and its worst bin gap. A Brier can be respectable while every stated probability is wrong |
| trading profitability | **not measured here.** It is a function of acquisition cost, per-level fees, fill probability and size, none of which is an input to this measurement. It is the entry lane's question and a separate verdict |

**"300 events and ≤ .24" is a declared policy threshold, not proof of
calibration**, and the declaration says so. 0.25 is the score of a constant
0.50 forecast — exactly, for any outcome set — and nothing more; it is
**not** the no-skill floor of an arbitrary market, which is why it is
labelled `THE_SCORE_OF_A_CONSTANT_0.50_FORECAST_NOT_A_UNIVERSAL_NO_SKILL_FLOOR`
and is never a criterion. On a book of heavy favourites the no-skill score
is far lower, and that is exactly how the previous evaluator passed 300
forecasts of .60 on fixtures that won 90% of the time.

Holding out a baseline costs sample, and the cost is stated rather than
avoided: `total_resolved_fixtures_required` is 450, because one in three is
spent fitting and never scored.

**The input did not exist before this release.** `JOIN_OUTCOME` had been in
migration 103 since the beginning with no caller, so `outcome_known` was
false on every row ever written and the measurement had an empty input by
construction. `join_outcomes` now runs every cycle, bounded, through the
venue resolution reader. A short leg is scored against **one minus** the
venue's settlement price, mapped through the verified venue-side identity
(§4c) and not through the probability's complement flag. A price the
venue's own settlement endpoint returns that is neither 0 nor 1 is a
**confirmed void**; a name, an inference from converged prices and an
unparseable value are three other things and stay three other things.

**A shortfall is a result and is not written.** `to_row` refuses to produce
a row from anything that is not a completed verdict, so an insufficient
sample returns the number of events still needed and the gate stays exactly
as shut as before. A provisional score is reported so collection can be
watched, and named provisional so nothing treats it as a verdict. A FAILED
verdict **is** written — it is a measurement — and does not open the gate.

It cannot be cleared by editing a boolean in a worker, and it must not be.

---

## 6 · Evidence, controlled-test and production kept apart

### Controlled integration (Postgres, `tests/test_the_entry_lane_reaches_inventory.py`)

Only the provider payload, the venue ladder, the venue's rules prose and
the fixture metadata are supplied — a test cannot wait for a real market
to offer an edge. Everything between them is production code: the mapping,
the de-vig, the settlement comparison, the scope gate, the execution
estimate, the frozen sizing policy, the risk engine, the entry gate and
the four writes.

| assertion | result |
|---|---|
| with **no** calibration row, everything else clears and the entry is still refused on `MODEL_TRUST_DRIFT`, with no inventory created | ✅ |
| with a calibration row supplied, the decision becomes a position, an order, a fee-bearing fill and reconciled accounting | ✅ |
| the order is `TAKER` / `MARKETABLE_RECONSTRUCTED`, `queue_share` 0, `is_modelled` true | ✅ |
| cost basis includes fees; residual equals the position; realised P&L is 0 at entry | ✅ |
| a second cycle holds **one** position and **one** order | ✅ |
| an INCOMPATIBLE settlement rule creates no inventory | ✅ |
| an unreadable ladder and a ladder priced beyond break-even refuse by **different** names | ✅ |
| the gate's edge is computed on the **walked cost** with the **realised per-level fees**, the ORDER carries the submitted limit, the reservation carries the worst case, and all three are different numbers on the same row | ✅ |
| two scheduled management cycles, restart recovery, and the same position throughout | ✅ |
| **settlement by production code**: the only thing supplied is `client.markets.settlement(slug)`; production reads it, maps it, closes the residual, writes the outcome and releases the rails | ✅ |
| settlement asked the venue **once**, for the contract this position holds, with the odds fetch rigged to raise | ✅ |
| a second pass, on a new connection, does not re-read the venue, does not write a second row and does not change `written_at` | ✅ |

### The review's own counterexamples, as tests

| counterexample | result |
|---|---|
| probability .75, VWAP .625, fee .03125 → the gate must see .625 and admit, not .71875 and refuse | ✅ |
| venue YES settles 1, exposure held SHORT → outcome 0, and the accounting loses the whole basis | ✅ |
| 300 forecasts all .60 where 90% won → FAILED on `beats_the_held_out_baseline` **and** `calibration_error_at_or_below`, with the .24 ceiling genuinely cleared | ✅ |
| 150 fixtures represented by both payouts → 150 fixtures, 300 payout statements, INSUFFICIENT | ✅ |
| `RESOLVED_DERIVED` with a named winner → not a void, not an outcome, nothing written | ✅ |
| an outcome with no recorded basis → `UNVERIFIED_OUTCOME_PROVENANCE`, excluded from calibration | ✅ |
| migration 118 reopens a row joined by the uncorrected mapping, preserves `prior outcome=1`, and the corrected mapper returns 0 | ✅ |

### Production

Filled in from the dispatched run — see the run table at the end.

---

## 7 · Faults found and fixed by doing this

| fault | consequence |
|---|---|
| `bettor_risk_engine` returned `permitted: True`, `EXPOSURE_NEUTRAL` for any action outside the EV vocabulary — including the entry gate's own `BUY` — with no rail evaluated | an entry would have cleared risk because its action name was unrecognised |
| the provider's `observed_at` is ISO-8601; read raw it made the freshness gate report "one clock is not measured" for every candidate and made `context_for` **raise** | a wrong refusal that looked like a venue problem, and a crash in a scheduled loop |
| `outcome_index` had no source and would have defaulted to 0 | a long and a short on one market would have collided on the one-position index |
| the venue's published rules text was never fetched | every entry candidate's settlement comparison was UNKNOWN for want of a read |
| sizing the quantity at the break-even limit | a cheap full fill reported UNFILLED NOTIONAL |
| the price handed to the gate was the best level while the size spanned several | understated cost; depth became edge |
| `$13::date` bound as a `date` object | the acquisition route returned a bare HTTP 500, which read as "the scope evidence cannot be acquired" when the payload was fine |
| the market title fallback took only the first non-empty of `title`/`event_title` | the binding was refused on exactly the markets the route exists for |
| `input_chain` arrives as a JSON string | `jq` exited 5 and truncated the evidence print mid-decision, which read as a finding |
| a settled position stayed in the exposure sums | exposure accumulates forever and eventually refuses every entry on a flat book |
| `read_resolution` carried the venue's raw settlement string only under `outcome`, which holds a label on another branch | a consumer recording "what the venue said" recorded our float reading of it instead, losing the unit convention the raw string carries |
| recording a void as `outcome_known` with a NULL outcome violates migration 103's CHECK | the write raised, was counted as an error, and the void was re-read every run forever |
| a handful of fixtures the venue reports only as a named winner would occupy the whole per-run join budget, ordered by `decided_at` | every later fixture starved. The queue is now ordered by when the venue was last asked, never-asked first |

### Not ours, but a hazard on the release route itself

`render-ops.yml` is **511,578 bytes** against GitHub's 512,000-byte
workflow ceiling — 422 bytes of headroom. `tests/test_workflow_size_guard.py`
fails on it (2 tests), and it failed before this batch; nothing here
touched that file. It matters because `render-ops` **is** the API-only
release route: one ordinary added comment would push it over and GitHub
would refuse to load the workflow at all, leaving no deploy path. It needs
splitting, and that is its own change rather than something to fold into a
correctness batch.

---

## 8 · The capped pilot proposal

**Development status: the external-valuation shadow lane is complete and
released. The entry path is blocked on one named, in-system measurement.**

A capped live-capital pilot would need, in order:

1. **The calibration measurement** above. Until it exists the lane refuses
   to create even shadow inventory, and it would be wrong to fund a
   valuation whose accuracy has never been checked against outcomes.
2. **An exit rule.** This lane has none. `MAX_CAPITAL_HOURS` is measured
   as accrued rather than forecast precisely because of that, and
   inventory that cannot be exited by a stated rule should not be created
   with real money.
3. **An owner-approved limit set against a named funded account.** The
   limits in §4 are shadow limits derived from a frozen shadow notional.
   They are not a capital authorization and this release says so in the
   payload itself.
4. **Account selection.** The accounting-uncertain account remains paused.
   A different pilot account must be explicitly identified and authorized;
   nothing here selects one.

Items 1 and 2 are engineering and are ours. Items 3 and 4 are the owner's
decision and are the only things this record asks for.

---

## 9 · Runs

| run | SHA | what it establishes |
|---|---|---|
| (filled in on dispatch) | | |
