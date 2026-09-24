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

**The smallest action that clears it:** measure PINNACLE_DEVIG_V1 against
resolved outcomes over a stated window and write one row carrying the
window, the sample size, the metric, the score, the tolerance and who
measured it. A `sample_size` of 0 is refused by a CHECK.

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
