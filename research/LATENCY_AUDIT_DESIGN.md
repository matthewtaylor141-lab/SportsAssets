# Edge-decay / latency-cost audit — MEASUREMENT DESIGN, awaiting owner approval

**Owner question:** at the earliest moment BETTOR could realistically act, how
much of RN1's economic edge still existed?

**Status: DESIGN ONLY. No expensive query has been run. No number in this file
is a result.** `mirror_live=false`. No production change, no data repair.

`lat_cost = $41,430.00` is a **claim to be tested**, not a target to reproduce.
The independent estimator below never reads `ai_trades`. Runs 79–83 may not use
it, or any TRUEEDGE-derived field, to repair clocks, select population, define
prices, define quantities, or calculate economics (§3, the independence rule).
It becomes readable in run 84 only, as the thing being compared against.

**Run 79 is APPROVED and no further.** Runs 80–84 are designed, not approved;
run 79's findings are returned for review before anything else is dispatched.

---

## 1. The exact raw tables and fields proposed

### Source side — RN1's own fills (register 1)

| table | fields | role |
|---|---|---|
| `trades` | `id, whale_id, tx_hash, asset, condition_id, side, outcome, outcome_index, size, price, notional, market_slug, event_slug, sport, ts, source, detected_at, enriched_at, venue_seen_at, dedupe_key` | the source event; `ts` is his fill clock, `detected_at` ours |
| `positions` | `condition_id, token_id, net_shares, avg_cost, realized_pnl, notional_in, resolved` | his state, for cohort definition only |

### Observation side — the books actually retained

| table | fields | role |
|---|---|---|
| `copy_probes` | `trade_id, asset, side, his_price, his_size, his_notional, fill_ts, probe_at, reaction_s, book_ok, best_ask, best_ask_usd, slippage_cents, vwap_1k, vwap_5k, fillable_1k, fillable_5k, residual_roi_1k, residual_roi_5k, depth (jsonb), error` | **the only fill-level book snapshot we retain**; `depth` = top 8 ask levels from `GET /book` on `clob_api_base` — **RN1's venue, not PMUS** |
| `price_path` | `row_id -> live_orders.id, t_s, ask, sampled_at` | the only retained *post-event* price series; offsets `(0, 30, 60, 120, 281, 600)` s |
| `mirror_shadow` | tick clock, `target, ledger_net, his_net, mark` | our reading of his state + a PMUS mark |
| `mirror_orders` | `bid_at_place, ask_at_place, ask_at_send, wire, price, qty` | PMUS book as we saw it at placement / immediately before an IOC send |

### Our own execution side

| table | fields | role |
|---|---|---|
| `mirror_orders` | `id, book_id, kind, side, intent, tif, post_only, his_level, price, wire, qty, order_id, state, venue_state, filled, booked_filled, avg_px, cash_usd, realized, maker, taker_at_placement, decision, fast, receipt (jsonb), placed_at, updated_at, done_at` | the live PMUS order record |
| `mirror_fill_answers` | `whale, condition_id, fill_id, fill_ts, detected_at, at, tick, book_id, order_id, name, cause, rest_id, fast` | **which fill of his the tick named, and when** — the best retained source→named clock |
| `mirror_books` | `flow_last_at`, book lifecycle | anchor clocks |
| `live_orders` | `trade_id, us_market_slug, lane, his_price, reaction_s, limit_price, requested_usd, requested_shares, order_id, status, filled_shares, fill_price, filled_usd, raw (jsonb), placed_at, payout, pnl, settled_at` | the pre-mirror entry sleeve; `price_path`'s anchor |

### Settlement

`markets.resolved, markets.resolved_prices, markets.resolved_at`,
`market_tokens.token_id, outcome_index` — the payout that turns a price
difference into dollars.

### Mapping

`us_premap` (PMUS mapping state). **Caveat already established:** `us_premap` is
CREATEd outside `backend/migrations/`, so the migration catalogue knows only a
fraction of its columns; `research/check_sql.py` treats it as UNRESOLVED by
design (test fixture 6). Any use of it must name its columns from the worker,
not from the catalogue.

### The audit target — READ IN RUN 84 ONLY

**Out of bounds for runs 79-83 under the independence rule in §3.**

`ai_trades` (`trade_id, whale_username, asset, condition_id, side, his_price,
his_notional, reaction_s, clip_target, filled_notional, fill_vwap, shares,
slippage_cents, status, placed_at, payout, pnl, counterfactual_pnl,
settled_at`). **This is TRUEEDGE's entire input.** It is not an input to the
independent estimator.

---

## 2. Evidence / provenance status of each field

The Run 78 discipline applies unchanged and is not collapsed:

    COLUMN EXISTS -> PRODUCTION WRITE SITE EXISTS -> HISTORICAL POPULATION
      EXISTS -> SEMANTICS VERIFIED -> ELIGIBLE FOR **THIS SPECIFIC ESTIMATOR**

The last stage is narrower than Run 78's. A field can be eligible for
attribution and ineligible here: `mirror_shadow.mark` is a verified PMUS quote
and still cannot serve as an *executable* price, because a mark is not a depth.

**Stages 1–2 (code side)** are extended in `research/evidence_tiers.py` — the
`FIELDS` list and the `SEMANTICS` registry grow to cover every field in §1, and
statement 0 of the run-79 SQL is generated from the registry so the two halves
cannot drift.

**Stages 3–5 (data side)** are measured by run 79's statement 0 before any
economics. Nothing below is a measurement; it is what the code read says, and
each row is a prediction the gate will confirm or refute.

| field | stage-2 read from code | note |
|---|---|---|
| `trades.ts` | WRITE_SITE (`ingestion/pipeline.py`, `ingestion/history.py`) | **mixed domain**: block timestamp on `source='chain'`, API timestamp on `source='poll'` — must be split by `source`, never pooled |
| `trades.detected_at` | WRITE_SITE — `datetime.now(tz=utc)` in the app process (`pipeline.py:128`) | app clock, **not** DB `now()` |
| `trades.venue_seen_at` | **WRITE_SITE** — `ingestion/pipeline.py:184`, inside `ON CONFLICT DO UPDATE`: `now()` stamped only when `EXCLUDED.source='poll'` AND the same whale, first stamp wins | **I predicted "no write site" and was wrong** — an earlier hand grep filtered too narrowly and the code-side gate caught it. Semantics stay UNVERIFIED: it is stamped only on the poll lane, so its NULLs mean "chain-only, or never re-seen", never "not seen by the venue" |
| `copy_probes.probe_at` | WRITE_SITE (`copy_probe.py:110`) | stamped **before** the semaphore and **before** the HTTP book GET |
| `copy_probes.depth` | WRITE_SITE | **top 8 ask levels only** — a hard ceiling on measurable depth |
| `copy_probes.reaction_s` | WRITE_SITE | `probe_at − fill_ts`: a **cross-domain subtraction** (see §3) |
| `price_path.ask` / `t_s` | WRITE_SITE (`workers/price_path.py`) | anchored at `live_orders.placed_at`, **not** at his fill |
| `price_path.sampled_at` | **DB_DEFAULT** — `DEFAULT now()` (migration 042); the worker's INSERT never names it | a third stage-2 verdict, added because the name rule alone called this UNAVAILABLE while it is populated on every row (see below) |
| `mirror_orders.placed_at` | WRITE_SITE, `DEFAULT now()` at the INSERT | written with `state='placing'` — **before the venue send** |
| `mirror_orders.updated_at` | WRITE_SITE, but **mutable** | the `state='open'` write is the nearest ack proxy and is overwritten by every later update -> ack time not durably retained |
| `mirror_orders.done_at` | WRITE_SITE, terminal only | |
| `mirror_orders.ask_at_send` | WRITE_SITE (059) | NULL on every rest by design; after the MAKER lane nearly everything is a rest -> population predicted thin |
| `mirror_orders.receipt` | WRITE_SITE | **unprobed**: whether the venue's response carries any venue-side timestamp is unknown and is a run-79 probe |
| `mirror_fill_answers.*` | WRITE_SITE (060/061) | populated only from 2026-09-08 |
| **fees** | **no fee column exists in `backend/migrations/`** | **NOT IDENTIFIABLE FROM RETAINED DATA.** The $1 pair probe (task #126) is the only path to a measured fee |
| `mirror_orders.trigger_trade_id`, `his_fill_ts`, `first_fill_at` | NO_WRITE_SITE (Run 78) | remain NOT IDENTIFIABLE; not resurrected here |
| `his_fill_id` | WRITE_SITE, semantics UNVERIFIED (Run 78 §5) | **not promoted** for this estimator either |

**A COLUMN DEFAULT IS A WRITE SITE, AND THE APP'S SQL NEVER NAMES IT.** Running
the extended code-side gate turned up `price_path.sampled_at` as NO_WRITE_SITE:
the worker sends `INSERT INTO price_path (row_id, t_s, ask) VALUES ($1,$2,$3)`
and the column carries `DEFAULT now()`. By the name rule alone the field would
have been declared UNAVAILABLE while being populated on every row — **a false
UNAVAILABLE, which is not the safe direction: it silently deletes real
evidence.** `evidence_tiers.py` now emits three stage-2 verdicts —
`WRITE_SITE` / `DB_DEFAULT` / `NO_WRITE_SITE`. `DB_DEFAULT` is kept distinct
rather than folded into "written" because **the author is different**: the
database, at commit, on the server's clock (C3), not the application on the app
container's clock (C2) — and for a clock field that distinction *is* the
measurement.

Re-checked against Run 78: `trigger_trade_id`, `his_fill_ts` and `first_fill_at`
have no DEFAULT either, so they remain NO_WRITE_SITE and
**`CAUSAL_BRIDGE_CLOSED.md` conclusion 4 is unaffected.**

Any stage failing leaves its component **NOT IDENTIFIABLE**, printed as such.
No component is allowed to appear as `0.00`.

---

## 3. The clock domains

Four domains, and they are not interchangeable.

| id | domain | generated by | represents |
|---|---|---|---|
| **C1** | RN1 venue / chain | Polygon block producer, or the whale venue's API | `trades.ts` — block time on `source='chain'`, server time on `source='poll'`. **Two different things in one column.** |
| **C2** | BETTOR app process | `datetime.now()` inside the worker/API container | `trades.detected_at`, `copy_probes.probe_at`, the mirror tick's own clock |
| **C3** | Postgres server | `now()` / `DEFAULT now()` | `mirror_orders.placed_at / updated_at / done_at`, `live_orders.placed_at`, `price_path.sampled_at`, `service_heartbeats.beat_at` (`ai_trades.placed_at` is also C3 and is **out of bounds until run 84**) |
| **C4** | PMUS venue | the venue | **nothing retained today** — possibly inside `receipt` / `raw`; unprobed |

Known skew and known ambiguity, carried forward verbatim:

- `reaction_s = probe_at(C2) − fill_ts(C1)` has **median −0.73 s**. A negative
  reaction time is not a fast reaction; it is proof the two clocks are not
  comparable at sub-second resolution.
- `probe_at` is stamped **before** the HTTP fetch and the fetch's completion
  time was never retained. `probe_at` is therefore **not** the moment the
  displayed book was observable — it is a lower bound on it.
- `mirror_shadow` snapshots carry scan-completion / page-walk ambiguity: the
  tick clock is not the instant each market in the tick was read.
- C2 vs C3 skew is unmeasured (it is the same skew that makes the
  `test_l2_review_pins` since-label flaky, task #72).

**Rules, enforced in the SQL, not in prose:**

1. Every derived duration carries the pair of domains it crossed.
2. A same-domain subtraction may produce a point estimate.
3. A cross-domain subtraction produces an **interval** and the label
   `CLOCK_UNRESOLVED`, and may never be reported to sub-second precision.
4. Run 79 measures two things that may partially repair this and are cheap:
   (a) **a search for an INDEPENDENT C2↔C3 bridge** — a table carrying an
   app-supplied timestamp AND a DB-defaulted timestamp written by the SAME
   statement. Run 79 enumerates the candidates and reports the skew only from
   one that qualifies. **If none qualifies, the answer is `CLOCK_UNRESOLVED`** —
   the skew is not repaired by guessing and not repaired by the audited system;
   (b) the C1 domain split, by profiling `detected_at − ts` separately for
   `source='chain'` and `source='poll'`.
5. No latency figure is quoted more precisely than its worst input's
   uncertainty.

**THE INDEPENDENCE RULE — owner correction, 2026-09-11, runs 79 through 83.**

> Runs 79–83 must not use `ai_trades`, or any TRUEEDGE-derived field, to repair
> clocks, select population, define prices, define quantities, or calculate
> economics.

An earlier draft of this file proposed bridging C2↔C3 with
`copy_probes.probe_at` vs `ai_trades.placed_at` on the same `trade_id`. **That
is withdrawn.** It would have calibrated the independent estimator's clock using
a column written by the system under audit — the estimator would then share a
failure mode with its subject, and a systematic error in the paper-trader's
write path would be invisible to exactly the check meant to catch it. An
unrepaired clock reported as `CLOCK_UNRESOLVED` is a weaker result and an honest
one; a repaired clock borrowed from the audited system is a stronger-looking
result that proves less.

`ai_trades` becomes readable in **run 84 only**, for external comparison. An
`ai_trades`-based clock diagnostic computed there is labelled
**CORROBORATING / AUDIT-SIDE EVIDENCE** and never folded back into the
independent estimator or into runs 79–83's figures.

---

## 4. The exact population definition

Nested, each step printed with count, conditions and **dollars**, so attrition
is visible rather than absorbed:

    U0  RN1 fills in the retained window
    U1  ∩ a defensible source clock (source known, ts non-null)
    U2  ∩ an exact fill-level book observation
          (copy_probes.trade_id = trades.id, book_ok, error IS NULL)
    U3  ∩ PMUS-mappable at the time of the event
    U4  ∩ settlement known (markets.resolved with a payout for the token)

The **common-unit cohort** for Phase 5 is `U2 ∩ U4`: both settlement-realized counterfactual margin and
post-detection price deterioration measurable on **the same events**.

Inherited selections, printed every time, never silently carried:

- `copy_probes` fires on **BUY only** (`copy_probe.py:101`) — the SELL side of
  his flow is absent from this instrument entirely.
- `copy_probes` fires only when `latency_s <= MAX_REACTION_S = 120 s` — late
  detections are structurally missing, which **biases the cohort toward our
  fastest detections**. This is the single most important selection in the study
  and it cuts in the direction that flatters us.
- Both `copy_probe_enabled` and `ai_trader_enabled` are env switches; periods
  with either off are absent.
- Whale must be in `source_whales() | vetting_whales()` at the time.

**No borrowing.** One fill never uses another fill's probe, book, or price path.
A missing observation is a `missingness_reason`, never a substitution from a
neighbour. (This is the exact-fill-coverage rule that already bit us once.)

---

## 5. The equations

All per event *i*, in **cents per share** and **dollars**, on one size.

Let `p_h` = his fill price, `q_h` = his shares, `N_h = p_h·q_h`, `q` = the
shares BETTOR would have wanted (the copy ratio applied to `N_h`, then the same
production clip rules), `payout_i ∈ {0,1}` from settlement.

**THE DECOMPOSITION — owner's form, 2026-09-11, and the only one used.** The two
execution components are **disjoint** and are subtracted separately. An earlier
draft of this file subtracted a total drag AND a slippage term, which counted
the depth cost twice; that draft is superseded here.

    SETTLEMENT_REALIZED_CF_MARGIN_i
                                = (payout_i − p_h) · q

    best_ask_at_action_i(d)     = top-of-book ask on the book observed at the
                                  defensible action time t_i + d

    depth_walked_vwap_i(d)      = VWAP to fill q from that same book
                                  (top-8 ceiling applies)

    TOP_OF_BOOK_MOVE_i(d)       = (best_ask_at_action_i(d) − p_h) · q

    DEPTH_SLIPPAGE_i(d)         = (depth_walked_vwap_i(d)
                                   − best_ask_at_action_i(d)) · q

    TOTAL_EXECUTION_DRAG_i(d)   = TOP_OF_BOOK_MOVE_i(d) + DEPTH_SLIPPAGE_i(d)
                                = (depth_walked_vwap_i(d) − p_h) · q

    IDENTIFIABLE_FEES_i         = UNKNOWN (no retained fee field)

**The two permitted downstream forms, and only these two:**

    REMAINING_MARGIN_i(d) = SETTLEMENT_REALIZED_CF_MARGIN_i
                            − TOTAL_EXECUTION_DRAG_i(d)
                            − IDENTIFIABLE_FEES_i

    REMAINING_MARGIN_i(d) = SETTLEMENT_REALIZED_CF_MARGIN_i
                            − TOP_OF_BOOK_MOVE_i(d)
                            − DEPTH_SLIPPAGE_i(d)
                            − IDENTIFIABLE_FEES_i

They are equal by construction. **Never subtract the total AND its components** —
that is the double count this correction removes, and any expression carrying
`TOTAL_EXECUTION_DRAG` beside either component is wrong by construction.

**CLOSURE ASSERTION (run 81, before any economic figure is printed).** Per event
and in aggregate, to the cent:

    |  (TOP_OF_BOOK_MOVE + DEPTH_SLIPPAGE)
       − (depth_walked_vwap − p_h) · q  |            <= $0.005

    |  form-1 REMAINING_MARGIN − form-2 REMAINING_MARGIN  |  <= $0.005

Printed with a **witness count** — the number of events actually asserted on. A
zero violation count beside a zero witness count is **NOT TESTED**, never PASS.
If either assertion fails, no downstream economic figure is reported until the
cause is named.

Reported alongside, never inside the subtraction:

    CROSS_VENUE_BASIS_i(d)      = (PMUS_best_i(d) − RN1venue_best_i(d)) · q
                                  -- only where BOTH observations exist at a
                                     defensible common time. It is a VENUE
                                     CHOICE term, not an execution cost: it says
                                     which book the above should have been
                                     measured on, and double-counts against
                                     TOP_OF_BOOK_MOVE if added to it.

**THE NAME IS LOAD-BEARING — owner correction, 2026-09-11.**
`(payout − p_h)·q` is **not** "RN1's gross edge" and must never be called that.
It is settlement-dependent and directional: it is what one directional position
happened to realize once the market resolved. It is named
`SETTLEMENT_REALIZED_CF_MARGIN` everywhere, and an earlier draft's
`RN1_GROSS_EDGE` is retired.

It is **categorically separate** from the settlement-independent matched-pair
mechanism already established in register 1:

    MATCHED_PAIR_GROSS_PNL = M · (1 − v_Y − v_N)

`MATCHED_PAIR_GROSS_PNL` is a structural property of a completed pair — it does
not depend on which side wins. `SETTLEMENT_REALIZED_CF_MARGIN` depends on
nothing else. **They are never added, netted, compared as though commensurable,
or merged into one "edge" figure merely because both are denominated in
dollars.** Any statement that treats the second as evidence about the first is a
register violation (`THREE_REGISTERS.md`).

The TRUEEDGE audit (run 84) **may** use `SETTLEMENT_REALIZED_CF_MARGIN`, because
that is the quantity TRUEEDGE's `counterfactual_pnl` appears to compute — but it
is then a statement about what TRUEEDGE evaluates, and must never be presented
as RN1's structural matched-pair edge.

**Definitional consequence.** `SETTLEMENT_REALIZED_CF_MARGIN` is ex-post by
construction, so it is the *size of the prize* and never a decision-time
predictor or a segmentation variable (§6's last rule). A mark-based alternative
at a defensible horizon is possible and would change every remaining-margin
figure; it is not used unless the owner asks.

**Sign convention.** Positive `TOP_OF_BOOK_MOVE` and positive `DEPTH_SLIPPAGE`
both mean *worse for us*. Both are written on the **ask**, so both are BUY-side;
the SELL-side mirror image on the bid is out of scope here because the retained
instrument (`copy_probes`) is BUY-only (§4).

**One `q`, four terms.** The same `q` appears in all four. Where the retained
book cannot fill `q`:

- if the top-8 book fills only `q' < q`, the event is `DEPTH_EXHAUSTED` and is
  reported at `q'` **with every term recomputed at `q'`**, never a blend;
- a `q'`-based row is carried in its own bucket and is never pooled with
  full-size rows in a per-share or percentage figure.

**Unknown propagation, mandatory.** Any UNKNOWN term makes `REMAINING_MARGIN` an
**UPPER BOUND**, labelled with the direction. An unknown is never zero. Fees are
unknown today, so **every remaining-margin figure this study produces is an
upper bound and will say so.**

**What the identity check does and does not prove.** Per event,
`TOP_OF_BOOK_MOVE + DEPTH_SLIPPAGE = (vwap − p_h)·q` telescopes — it is
algebraically true whatever the inputs are. Running it therefore verifies **data
handling**, not economics: that both components came from the *same* book
snapshot, at the *same* `q`, on the *same* event, with no borrowed row and no
silent NULL. It is worth running for exactly that and is not evidence that the
components are the right ones. If it fails to close to the cent the cause is a
join or a NULL, and nothing downstream is reported until it is named.

**Percentages** are only ever the ratio of two dollar figures **on the same
event set**. The prior comparison error is pre-registered as forbidden: RN1's
1.383% (structurally eligible matched cohort) and the 3.957% replication drag
(side-forced BUY cohort) have different populations and different denominators
and **may not be subtracted**; run 81 rebuilds both on `U2 ∩ U4` or reports
neither.

---

## 6. Known historical coverage limitations

- **Delay buckets.** `price_path`'s offsets are `(0, 30, 60, 120, 281, 600)` s.
  Therefore: `+1 s`, `+2 s`, `+5 s`, `+10 s` and `+300 s` are **NOT IDENTIFIABLE
  from price_path**. 281 s is 281 s and is not rounded to 5 minutes. The
  requested `+1/+2/+5/+10 s` buckets can only come from the probe snapshot at
  its own single instant, which is one observation, not a series — so those
  buckets will be marked NOT IDENTIFIABLE unless a second instrument is found.
- **`price_path` is anchored at `live_orders.placed_at`, not at his fill**, and
  is keyed to the pre-mirror entry sleeve. Re-anchoring to the source event
  requires `live_orders.trade_id -> trades.id`, and the result carries the
  anchor difference as an uncertainty, not as zero.
- **The probe book is RN1's venue**, not PMUS. Every replication figure from it
  is a register-2 figure and must say so (`THREE_REGISTERS.md`).
- **Depth is the top 8 ask levels.** Any `q` that consumes more than 8 levels is
  `DEPTH_EXHAUSTED`, not "filled at the last price".
- Mirror books span **5 of 36 days** (2026-09-06 00:40 → 09-10 16:45);
  `mirror_shadow` from 09-02 18:44; `mirror_fill_answers` from 09-08.
- **No fee data. No PMUS venue-side timestamps. No Kalshi book.**
- `game_start` / pregame-vs-live segmentation is only attempted if `game_start`
  is legitimately available; the mapper-selected **1.15%** sample is **not**
  representative and will not be used as if it were.
- No ex-post variable (settlement outcome, later price, final position) may be
  used as a decision-time predictor. Segments are defined on decision-time
  information only.

---

## 7. Identifiable vs unknown, by funnel component

Preliminary, from the code read; every row is confirmed or refuted by run 79's
gate before any economics.

| | component | status | what it rests on |
|---|---|---|---|
| A | source -> detection | **PARTIAL, CLOCK_UNRESOLVED** | `detected_at(C2) − ts(C1)`, split by `source` |
| B | detection -> decision | **PARTIAL** | `mirror_fill_answers.at − detected_at`, tick-granular |
| C | mapping / internal processing | **PARTIAL** | tick timing block; per-market read time not retained per market |
| D | decision -> submit | **PARTIAL** | `mirror_orders.placed_at` is the **pre-send** stamp; the send itself is unstamped |
| E | submit -> ack | **NOT IDENTIFIABLE** | no ack column; `updated_at` is mutable. Upgradeable only if the `receipt` probe finds a venue timestamp |
| F | ack -> fill | **NOT IDENTIFIABLE** | `first_fill_at` has no write site (Run 78) |
| G | market movement / replication drag | **IDENTIFIABLE at the probe instant** on his venue; PARTIAL on PMUS | `copy_probes.depth`, `price_path` |
| H | cross-venue basis | **PARTIAL** | needs two observations at a defensible common time; retained rarely |
| I | depth / size slippage | **IDENTIFIABLE, bounded** | top-8 ceiling; `DEPTH_EXHAUSTED` above it |
| J | fees | **NOT IDENTIFIABLE** | no field; awaits the pair probe |

These are **not** to be summed into one number called "latency" merely because
TRUEEDGE's output has one name.

### Pre-registered tests of the $41,430 claim — hypotheses, not findings

Read from the write site (`copy_probe._place_ai_trade`,
`analytics/engine.settle_ai_trades`, `api/app.py:9811`). **None has been
evaluated against data.** Each is a falsifiable check run in Phase 8:

1. **Venue.** TRUEEDGE's book is `GET /book` on `clob_api_base` — RN1's own
   venue. Test: does `lat_cost` contain any PMUS observation at all?
2. **Population.** `cf_on_filled` filters `filled_notional > 0` with **no**
   status filter; `paper_actual` filters `status='settled' AND filled_notional
   > 0`. Test: how many rows are filled-but-unsettled, and what do they
   contribute to the difference?
3. **Algebra.** On a fully-filled row, `cf − pnl` reduces to
   `payout · clip · (1/p_h − 1/p_us)`. Test: is the aggregate therefore carried
   entirely by winning rows, and does it contain any clock term at all?
4. **Size.** `counterfactual_pnl` uses `clip_target`; `pnl` uses actual
   `shares`. Test: what does the partial-fill size mismatch contribute?
5. **Fees.** Test: confirm no fee enters either side.

The verdict vocabulary is fixed in advance: **VALIDATED / PARTIALLY VALIDATED /
OVERSTATED / UNDERSTATED / NOT IDENTIFIABLE.** Agreement is not forced and
disagreement is not explained away.

---

## 8. The proposed sequence of runs

Serialized, cheapest first; each run's output gates the next. Every file passes
`research/check_sql.py` (all four layers, including the duplicate-CTE check)
before dispatch.

| run | contents | cost |
|---|---|---|
| **79** | Evidence gate only: statement 0 over every §1 field; the raw clock-domain inventory; the `receipt`/`raw` key probe for a venue timestamp; the **search** for an independent C2↔C3 bridge (`CLOCK_UNRESOLVED` if none); the C1 domain split by `source`; the U0→U4 population attrition with dollars. Every test prints its witness count. **No economics, no `ai_trades`.** | small |
| **80** | The funnel A–J decomposed where run 79 says identifiable, each with its clock-domain pair and `CLOCK_UNRESOLVED` where it applies. | medium |
| **81** | The common-unit cohort (`U2 ∩ U4`): per event, dollars, with the decomposition identity closing to the cent. Rebuilds 1.383% and 3.957% on one population or reports neither. | medium |
| **82** | The edge-decay curve at the **supported buckets only**; count / notional / gross edge / executable edge / deterioration \$ / deterioration ¢-per-share / % remaining / % still positive / median / p25–p75 / p10–p90 / depth coverage / missingness, then the segments. | large |
| **83** | ENGINEERING-FIXABLE vs VENUE/NETWORK-LIMITED vs MARKET-STRUCTURAL vs UNKNOWN, each classification carrying the code site or the measurement that proves where the time went. Nothing is called fixable merely for happening after detection. | medium |
| **84** | The TRUEEDGE independent audit: population overlap, missing rows each direction, dollar/sign/price/quantity/timing differences, fee treatment, depth treatment, cross-venue treatment; then the verdict. | medium |

Phase 9's decision test (OUTCOME A / B / C) is stated only after run 84, and
only with X and Y measured rather than invented.

If any run returns a vacuous result — a zero violation count beside a zero
witness count — it is reported as **NOT TESTED**, exactly as gate 3 was in
Run 78.

`mirror_live=false` throughout. Nothing here changes production.
