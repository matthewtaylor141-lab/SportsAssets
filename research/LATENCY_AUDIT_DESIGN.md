# Edge-decay / latency audit — MEASUREMENT DESIGN, revision 2

**Supersedes revision 1 in full.** Revision 1 tried to estimate one quantity
called "latency". Run 79 showed that quantity does not exist in retained data as
a single measurable thing, so the audit is now **two separate estimators** that
are never added, netted, or presented as one number.

**Status: DESIGN ONLY, awaiting approval. Run 80 is NOT approved and has not
run.** No economics executed. `ai_trades` / TRUEEDGE untouched.
`mirror_live=false`. No production change, no data repair.

`lat_cost = $41,430.00` remains a **claim to be tested**, in run 84 only.

---

## 0. LOCKED — the run-79 findings

Owner order 2026-09-12. These are not re-opened; a contradicting later result is
a retraction with its own heading, never a silent overwrite.

**0.1 `trades.ts` is not one clock population.** Four lanes, four semantics:

| lane | what the `detected_at − ts` difference actually is | may it become physical reaction time? |
|---|---|---|
| `backfill` | archive ingestion (p50 ≈ 241 days) | **never a latency observation** |
| `chain` | clock-corrupted C1→C2 comparison; **91.7%** negative lag | **no** |
| `poll` | polling / sweep cadence | **no — never called execution latency** |
| `s1` | positive, tight, zero negatives, still crosses C1→C2 | **shape evidence only; CLOCK_UNRESOLVED as an absolute** unless independently calibrated |

**0.2 The original `U1` source filter is withdrawn** and is **not** replaced by
another guessed list. Every lane carries explicit semantics and is admitted only
to estimators for which those semantics are valid (§3).

**0.3 `price_path` does not overlap the mirror-trading period.**
`price_path` 2026-09-02 05:05 → 2026-09-04 23:15; `mirror_books` begins
2026-09-06 00:40. It may be analysed **only** as a separate pre-mirror /
entry-sleeve cohort and its decay curve is **never** generalised to the mirror
period.

**0.4 `t_detect` / `t_send` / `t_reply` (925 rows) are OUR timestamps, not PMUS
venue timestamps**, and pass the provenance and population gates in §7 before
any use. Non-mirror ⇒ engineering diagnostic / sensitivity only, unless
transportability is demonstrated.

**0.5 There is no retained PMUS venue timestamp.** submit→venue-ack and
venue-ack→fill are **NOT IDENTIFIABLE from venue-clock evidence.**

**0.6 Every future run is pinned to one immutable `AUDIT_CUTOFF_TS`** (§2) and
prints it. No population is ever compared across two drifting live reads.

**0.7 The 120 s gate discrepancy is investigated before `reaction_s` is used as
a selection descriptor** (§6).

---

## 1. THE TWO ESTIMATORS — and why they are separate

| | **Estimator A** | **Estimator B** |
|---|---|---|
| name | `FIRST_RETAINED_OBSERVATION_DRAG` | physical / engineering latency |
| question | at the first exact fill-level book we actually retained, how much worse was the independently observable executable price than RN1's fill price? | which pieces of elapsed time can be measured or bounded from retained clocks? |
| needs a physical reaction time? | **no** | that *is* the question |
| needs settlement? | **no** | no |
| unit | cents/share and dollars | seconds, per clock-domain pair |
| may be called "latency cost"? | **NO** | only where a component is POINT_IDENTIFIED |

A measures *how much*; B measures *how long*. Multiplying or narrating one as
the cause of the other is the error revision 1 built in.

---

## 2. `AUDIT_CUTOFF_TS` — immutable, printed, per-table

    AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z

Chosen because it is after run 79 completed (23:55:42Z) and before run 80, on a
round boundary. **It never moves.** Changing it creates a new named cutoff and
obliges a re-run of every statement that used the old one; results from two
cutoffs are never compared.

Applied as a predicate on **each table's own arrival clock**, not one global
filter:

| table | cutoff predicate | why this column |
|---|---|---|
| `trades` | `ts <= CUTOFF AND detected_at <= CUTOFF` | both the event and our sight of it must precede the cutoff |
| `copy_probes` | `probe_at <= CUTOFF` | the observation's own clock |
| `price_path` | `sampled_at <= CUTOFF` | the sample's own clock |
| `mirror_orders` | `placed_at <= CUTOFF` | the row's creation, not its mutable `updated_at` |
| `mirror_books` / `mirror_shadow` | `opened_at` / `at <= CUTOFF` | the tick's own clock |
| `markets` (settlement) | `resolved_at <= CUTOFF` | settlement **as known at the cutoff** — a market resolved after it counts as unsettled, so U4 cannot grow between runs |
| `live_orders` | `placed_at <= CUTOFF` | |

Run 79's U0 read 962,459 in one statement and 962,454 in another because five
rows arrived mid-run. Under this rule that cannot recur.

Every run prints the cutoff in its header **and** re-prints U0 beside it, so a
drift is visible rather than inferred.

---

## 3. REVISED UNIVERSES

The old chain `U0 ⊃ U1 ⊃ U2 ⊃ U3 ⊃ U4` is wrong because it made every estimator
inherit a source-clock filter that **Estimator A does not need**. Replaced by a
base population plus *per-estimator admission*.

    U0      RN1 fills at or before the cutoff.
            The denominator for every coverage percentage. No filter but the
            cutoff and the whale.

    LANE    Every row carries its ingestion lane verbatim (backfill / chain /
            poll / s1 / anything new). NOT a filter -- a CARRIED ATTRIBUTE.
            A lane that appears and is not in the matrix below halts the run
            rather than being silently pooled.

    U2      THE OBSERVATION UNIVERSE, and Estimator A's primary population:
              exact trade_id linkage to a copy_probes row
              AND book_ok
              AND error IS NULL
              AND p_h valid (> 0 and < 1)
              AND a depth array present
              AND probe_at <= CUTOFF
            U2 DOES NOT REQUIRE A DEFENSIBLE SOURCE CLOCK, and does not
            require settlement. It is defined by what we OBSERVED, not by what
            we can time.

    U3      PMUS-mappable. Run 79 used "ever mapped", a COARSE PROXY, and it is
            labelled as such until a time-causal mapping state is built.

    U4      Settlement known as at the cutoff. SECONDARY ONLY (§9).

    UB      Estimator B's populations, one per component, each defined by the
            clocks that component needs (§5). There is no single UB.

**`U1` no longer exists.** The concept it tried to express — "rows whose source
clock is defensible" — is not a property of a row; it is a property of a
(lane, estimator) pair, and it lives in the matrix below.

---

## 4. LANE × ESTIMATOR ELIGIBILITY MATRIX

`A` = `FIRST_RETAINED_OBSERVATION_DRAG`. `B-*` = Estimator B components.

| lane | A: drag | B: source→detection | B: detection→send | B: send→reply | notes |
|---|---|---|---|---|---|
| `backfill` | **ADMITTED** | **EXCLUDED** | EXCLUDED | EXCLUDED | A needs no source clock; the probe either exists or it does not. If a backfill row carries an exact probe it is a real observation. **Reported as its own stratum** so it can never silently dominate. |
| `chain` | **ADMITTED** | **EXCLUDED** — clock-corrupted, 91.7% negative | admitted (C2→C2) | admitted | `ts → detected_at` must never be rendered as seconds here |
| `poll` | **ADMITTED** | **CADENCE ONLY** — labelled `POLL_SWEEP_CADENCE`, never execution latency | admitted | admitted | |
| `s1` | **ADMITTED** | **SHAPE ONLY** — `S1_CROSS_CLOCK_SHAPE`, CLOCK_UNRESOLVED as an absolute | admitted | admitted | the only positive, tight, zero-negative lane |
| unknown/new | **HALT** | HALT | HALT | HALT | an unrecognised lane stops the run |

Admitting `backfill` to A is deliberate and is the point of separating the
estimators: A asks what the book looked like at a retained observation, and that
question does not care when we learned of the fill. **Its lane mix is printed in
every A output**, so if the drag is being carried by backfill rows that is
visible immediately rather than discovered later.

---

## 5. ESTIMATOR A — `FIRST_RETAINED_OBSERVATION_DRAG`

Per event *i* in U2, from **one exact probe snapshot**, never a borrowed book.

    p_h                  = RN1's source fill price (trades.price)
    OBSERVED_BEST_ASK    = copy_probes.best_ask from THAT probe
    DEPTH_WALK_VWAP(q)   = VWAP to fill q from THAT probe's depth array,
                           top-8 levels only

    TOP_OF_BOOK_MOVE          = (OBSERVED_BEST_ASK  − p_h) · q
    DEPTH_SLIPPAGE            = (DEPTH_WALK_VWAP − OBSERVED_BEST_ASK) · q
    TOTAL_OBSERVED_REPLICATION_DRAG
                              = (DEPTH_WALK_VWAP − p_h) · q
                              = TOP_OF_BOOK_MOVE + DEPTH_SLIPPAGE

**Closure assertion**, same event / same q / same snapshot, printed with a
witness count before any figure:

    | (TOP_OF_BOOK_MOVE + DEPTH_SLIPPAGE) − TOTAL_OBSERVED_REPLICATION_DRAG |
        <= $0.005     per event AND in aggregate

Zero violations beside zero witnesses is **NOT TESTED**. The identity
telescopes, so it proves *data handling* — one snapshot, one q, one event, no
borrowed row, no silent NULL — not the economics.

**It is `FIRST_RETAINED_OBSERVATION_DRAG`. It is not a latency cost.** It
contains whatever happened before the retained observation and says nothing
about how many physical seconds caused it.

### The size `q` — a decision I am flagging, not burying

`TOP_OF_BOOK_MOVE` per share is q-free; `DEPTH_SLIPPAGE` is not. Production's
sizing rule changed repeatedly across the window (ratio, $2,500 clip, per-game
cap added then removed), so using "the production rule" would inject that rule's
history into the measurement.

Proposed: **cents per share is the primary unit**, and dollars are reported at
three explicitly hypothetical sizes, none blessed:

    q_a = 10% of RN1's notional at p_h, uncapped   (the standing ratio, no clips)
    q_b = RN1's own shares                          (full-size replication)
    q_c = a fixed $1,000 clip                       (a small constant)

If you want one canonical q instead, say which and I will make it primary and
demote the rest to sensitivities.

**Depth exhaustion:** if the top-8 book fills only `q' < q`, the event is
`DEPTH_EXHAUSTED`, every term is recomputed at `q'`, and those rows are bucketed
apart — never pooled into a per-share or percentage figure with full-size rows.

**Fees remain UNKNOWN and separate** (no fee column exists anywhere in
`backend/migrations/`). A is a drag figure, not a net-of-cost figure.

### A's reporting block

Event count · conditions · source notional · shares · cents/share deterioration
· dollar deterioration · median / p25 / p75 / p10 / p90 · positive / zero /
negative deterioration fractions · depth-exhausted fraction · **source-lane
mix** · sport and market-type segmentation where decision-time-valid.

A **negative** drag (the book was better than his price) is a real, reportable
outcome and is never floored at zero.

---

## 6. ESTIMATOR B — physical / engineering latency

Every component carries one of exactly five labels, and no common timeline is
forced across incompatible clocks:

    POINT_IDENTIFIED · INTERVAL_IDENTIFIED · SHAPE_ONLY ·
    CLOCK_UNRESOLVED · NOT_IDENTIFIABLE

| component | clocks | proposed label | basis |
|---|---|---|---|
| source → detection, `chain` | C1→C2 | **CLOCK_UNRESOLVED** | 91.7% negative; never rendered as seconds |
| source → detection, `poll` | C1→C2 | **CLOCK_UNRESOLVED**, reported as `POLL_SWEEP_CADENCE` | measures the sweep, not a reaction |
| source → detection, `s1` | C1→C2 | **SHAPE_ONLY**, reported as `S1_CROSS_CLOCK_SHAPE` | positive, tight, zero negatives — a shape, not "4.159 seconds of latency" |
| source → detection, `backfill` | — | **EXCLUDED** | not a detection |
| detection → send | C2→C2, **two processes** | **INTERVAL_IDENTIFIED** (pending §7) | `t_send − t_detect`; same clock *kind*, different container |
| send → reply | C2→C2, **one process** | **POINT_IDENTIFIED** (pending §7) | `t_reply − t_send`, both `time.time()` in the executor |
| submit → venue ack | C4 | **NOT_IDENTIFIABLE** | no venue timestamp retained anywhere |
| venue ack → fill | C4 | **NOT_IDENTIFIABLE** | ditto; `first_fill_at` has no write site (Run 78) |
| mirror decision → placement | C3 | **NOT_IDENTIFIABLE as a send time** | `mirror_orders.placed_at` is stamped pre-send; `updated_at` is mutable |
| app ↔ DB skew | C2↔C3 | **FOUND BUT UNDERPOWERED** | n = 2, present-day only; constrains C2↔C3 only and cannot explain a C1 artifact |

**`t_reply − t_send` is a venue round trip, not an acknowledgement time.** It
bounds submit→ack from above; it does not identify it.

---

## 7. THE 120 s / `reaction_s` DISCREPANCY — RESOLVED FROM CODE

Not a plan. Read from the write sites:

    ingestion/pipeline.py:76
        latency = detected_at.timestamp() − ev.ts_epoch
        "latency_s": round(max(latency, 0.0), 3)          <-- CLAMPED AT ZERO

    copy_probe.py:104
        if latency is not None and float(latency) > MAX_REACTION_S: return
        MAX_REACTION_S = 120.0

    copy_probe.py:110,135
        probe_at = datetime.now(utc)                      <-- stamped later
        reaction = (probe_at − fill_dt).total_seconds()   <-- the STORED column

**They are two different quantities and the gate tests the earlier one:**

1. The gate variable is `latency_s = max(detected_at − ts, 0)` — **floored at
   zero**. Every one of the 180,958 negative-lag `chain` rows presents `0.000`
   to the gate, so **the gate never rejects a negative-lag row**, and
   `latency_s` is not the same quantity as `detected_at − ts` for that lane.
2. The stored column is `reaction_s = probe_at − ts`, and `probe_at` is stamped
   **after** the payload was built, downstream of detection. So
   `reaction_s ≥ detected_at − ts` always, by the detection→probe dispatch
   interval.
3. Therefore a row can pass at `latency_s = 119.8` and store
   `reaction_s = 127.189`. **127.189 is not a gate violation; it is the gate
   measuring a different, floored, earlier variable.**

Run 80 measures, rather than assumes: how many U2 rows have
`reaction_s > 120`, their notional share, their lane mix, and the distribution
of `reaction_s − max(detected_at − ts, 0)` (the dispatch interval, C2→C2,
same-domain, so a legitimate point estimate). **The gate is not silently
redefined**; both variables are printed side by side wherever either is used as
a descriptor.

---

## 8. PROVENANCE PLAN FOR `t_detect` / `t_send` / `t_reply`

Write site found — `live_executor.py:9465-9471`:

    _timing = {"t_send": time.time(), "t_detect": payload.get("detected_at")}
    result  = await _ioc_guarded(pool, row_id, pmus.submit_fok, ...)
    _timing["t_reply"] = time.time()

| field | author | clock | meaning |
|---|---|---|---|
| `t_send` | executor process | C2, `time.time()` | the instant the IOC call was made |
| `t_reply` | **same** executor process | C2, same clock instance | the instant the venue call returned |
| `t_detect` | **ingestion** process, carried through the payload | C2, **different container** | our first sight of the fill |

So `t_reply − t_send` is a **same-process** difference — a legitimate point
estimate of the venue round trip. `t_send − t_detect` crosses two app processes
and is an **interval**, not a point, unless co-location is demonstrated.

**Population — the gate that decides whether it may be used at all.** The same
file writes `_lane = "ioc"` (and a `rest` lane) and its own reads exclude
`COALESCE(lane,'') <> 'mirror'` (live_executor.py:1018, 6341). That is strong
code evidence the 925 rows are the **entry-sleeve lane, not the mirror** — but
run 80 **measures** the lane mix rather than asserting it, and also prints the
date span, the whale mix, and coverage against the lane's own denominator.

**If the population is non-mirror**, these fields are an **ENGINEERING
DIAGNOSTIC / SENSITIVITY ONLY** and are not promoted to the primary mirror
estimator merely because they exist. `analytics/lane_exec.py` already reads them
as `send_s` and `venue_rtt_s`; that existing reader is a use, not a validation.

---

## 9. SETTLEMENT-SELECTION TREATMENT

**U4 does not select Estimator A's population.** Execution-price deterioration
does not require settlement.

    PRIMARY    U2                for FIRST_RETAINED_OBSERVATION_DRAG
    SECONDARY  U2 ∩ U4           only where SETTLEMENT_REALIZED_CF_MARGIN
                                 = (payout − p_h) · q  is needed

`SETTLEMENT_REALIZED_CF_MARGIN` stays settlement-dependent and directional, and
stays categorically apart from the settlement-independent matched-pair
mechanism `MATCHED_PAIR_GROSS_PNL = M · (1 − v_Y − v_N)`. The two are never
added, netted, compared as commensurable, or merged because both are dollars.

Whenever the settlement-selected cohort is used, print beside it so the
selection stays visible: U2 count and notional · U2 ∩ U4 count and notional ·
**retention %** · sport mix · source-lane mix · price distribution · size
distribution.

**Settlement-dependent economics are never generalised back to U2 without
evidence.** Run 79 already shows the selection is not neutral: U3→U4 dropped
26,121 events and $7.29M, and settlement coverage varies by sport and horizon.

---

## 10. DECAY-CURVE RULE

No single historical decay curve is built. Two populations, temporally
disjoint, kept apart:

- **A. pre-mirror `price_path` cohort** — 5,734 rows, 2026-09-02 05:05 →
  09-04 23:15. Every output from it is labelled
  **`PRE_MIRROR_PRICE_PATH_SENSITIVITY`**. It cannot answer "how fast did edge
  decay while BETTOR was trading?"
- **B. mirror-period cohort** — 2026-09-06 00:40 onward.

If no repeated post-detection book series exists for the mirror period, the
answer is printed verbatim:

    MIRROR_PERIOD_TIME_DECAY_CURVE = NOT IDENTIFIABLE FROM RETAINED DATA

and the pre-mirror cohort is **not** substituted. Run 80 establishes which of
these holds by censusing repeated observations per event in the mirror window,
rather than assuming from `price_path` alone.

Offsets remain `(0, 30, 60, 120, 281, 600)` s: `+1 / +2 / +5 / +10 s` and
`+300 s` are NOT IDENTIFIABLE, and 281 s is 281 s, never rounded to five
minutes.

---

## 11. PROPOSED RUN 80 STATEMENTS — no economics, still

Run 80 is the **population and provenance run for the two estimators**. It
computes no drag and no latency figure. Economics begin at run 81, separately
approved.

| # | statement | output |
|---|---|---|
| 0 | cutoff header: `AUDIT_CUTOFF_TS`, U0 at the cutoff, and the same count re-read at end-of-run | a drift of 0 rows, proven not assumed |
| 1 | **lane census at the cutoff**: every distinct `trades.source`, count, notional, date span — and a HALT row if any lane is not in §4's matrix | no silent pooling |
| 2 | **U2 construction, step by step**: each admission predicate's effect in events / conditions / notional, so U2's definition is auditable rather than a single number | attrition with dollars |
| 3 | **U2 lane mix and coverage**, including the `backfill` stratum reported separately | |
| 4 | **depth-array census**: levels retained per probe, distribution, and the fraction where `q_a` / `q_b` / `q_c` would exhaust the top 8 | sizes A can honestly price |
| 5 | **the 120 s / `reaction_s` split** (§7): rows over 120, notional share, lane mix, and the dispatch-interval distribution | both variables side by side |
| 6 | **`t_*` population gate** (§8): lane mix, whale mix, date span, coverage of the 925 rows against their lane's denominator | mirror vs non-mirror, measured |
| 7 | **mirror-period repeated-observation census**: per event in the mirror window, how many distinct book observations exist at distinct times | decides whether §10's B cohort exists at all |
| 8 | **U2 ∩ U4 selection profile** (§9): retention %, sport mix, lane mix, price and size distributions | settlement selection made visible |
| 9 | **witness ledger** — every test beside the rows it examined; zero witnesses prints NOT TESTED with the reason | as run 79 |

Estimated cost: comparable to run 79 (~5 minutes), no JSONB dragged through any
materialised set.

---

## 12. What has NOT changed

The independence rule (runs 79–83 may not use `ai_trades` or any
TRUEEDGE-derived field to repair clocks, select population, define prices,
define quantities, or calculate economics; run 84 only, labelled
audit-side evidence). The five-stage evidence chain, never collapsed, with
stage 2's three verdicts `WRITE_SITE` / `DB_DEFAULT` / `NO_WRITE_SITE`. The
witness discipline. The unknown-propagation rule: an UNKNOWN term makes a result
an upper bound, never a zero. No ex-post variable as a decision-time predictor.
The 1.15% mapper sample is not representative.

`mirror_live=false`. Run 80 does not execute until approved.
