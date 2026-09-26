# Final acceptance record

Three verdicts, kept apart on purpose. Each one is answered by its own
evidence, and none of them is allowed to stand in for another.

| | question | verdict |
|---|---|---|
| **I** | Does the shadow software run the whole lifecycle correctly? | **ACCEPTED** |
| **II** | Is it finding admissible opportunities in current markets? | **GENUINE NO_TRADE** — 0 autonomous positions |
| **III** | Is capital qualified to be deployed? | **NOT QUALIFIED** |

Funded trading and funded submission are disabled. No order has been
submitted to any venue. The accounting-uncertain account stays paused. No
pilot account has been identified or authorized, and none was switched.

**Live release.** `bfa3c5f5baf0e85d0e9d97797d336eeae7ceb77c`, released by the
API-only route (`render-ops.yml` → `deploy-api-commit`, by commit id, which
cannot reach a worker). The worker service stays pinned at `f5d1c05` and was
not touched. Confirmed live by the entry loop's own writer identity in a
completed cycle, not by a deploy timestamp:

```
writer {"pid":1,"build":"bfa3c5f5baf0e85d0e9d97797d336eeae7ceb77c",
        "module":"sportsassets.workers.ext_pinnacle_loop",
        "source_sha256_12":"e4de0d0c56ef"}
state LIVE   at 1790381655.310432  = 2026-09-26T00:14:15Z
```

Readback run: `command-verify` 36204737612 ("run 71"), job 108298744143.

---

## I · Controlled software capability — ACCEPTED

This part proves the code runs end to end. It says **nothing** about
profitability, about current markets, or about capital.

### I.1 The positive lifecycle, with calibration absent and research mode armed

`backend/tests/test_the_research_lane_runs_the_lifecycle_uncalibrated.py`
(4 tests) drives the **current external-valuation research lane** — its own
entry writer, its crossing execution model, `run_continuing_management`, the
exit/settlement consumer and the accounting — not the July frozen benchmark.

Re-run for this record on a freshly migrated database, together with the base
lane file and the double-count file: **21 passed**.

What it asserts:

- **Control first.** With the control row deleted and no calibration measured,
  the lane refuses and writes 0 positions. Only then is research mode armed.
- **The waiver is real and bounded.** `authorised true`, `waived ==
  ["MODEL_TRUST_DRIFT"]`, and never `STALE_DATA`, `OUT_OF_DISTRIBUTION` or
  `UNRESOLVED_SETTLEMENT_SEMANTICS`.
- **Research provenance.** The position is stamped
  `UNCALIBRATED_RESEARCH_SHADOW`, which is a different value from the
  calibrated lane's `AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW`. The test
  asserts both the value and the inequality.
- **Supplied inputs are labelled as supplied.** Every market input in the test
  is stubbed; every order and fill carries `is_modelled` true, which migration
  100 CHECKs at the schema level.
- **Correct inventory after reload: 900, not 1800.** 900 contracts fill across
  3 fills; the reload resolves `assign_seed False` and `held() == 900.0`.
- **Recurring management**, twice; the second cycle adds no second position.
- **Settlement and reconciled P&L** through the venue reader.
- **Funded submission is unreachable**: the client has no `orders` attribute,
  `SELECT count(*) FROM live_orders` is 0, and `order_submitted is False`.

### I.2 Process recovery — verified, and described accurately

**A connection reload is not a process restart.** Re-reading a position on a
fresh database connection inside the same interpreter exercises
`reload_managed`; it does not exercise process recovery. There is no existing
process-restart test to lean on — `test_rn1x_persistence_pg.py` §4 is a second
cycle in the same process — so process recovery is verified directly, by two
real OS processes on a freshly migrated database. Re-run for this record:

```
A os.getpid 9357        enters, manages, EXITS
A pid    EXT_PINNACLE_DEVIG_V1_SHADOW:EXT_PINNACLE_ENTRY_V1:c-entry-sea-hou:0
A acct   filled 900.0   basis 586.47

B os.getpid 9362        a different interpreter, no shared state
B pid    EXT_PINNACLE_DEVIG_V1_SHADOW:EXT_PINNACLE_ENTRY_V1:c-entry-sea-hou:0
B examined_after_process_restart 1
B provenance UNCALIBRATED_RESEARCH_SHADOW
B assign_seed False   held 900.0
B orders 1   fills 3
B settled 1   venue_calls ['aec-mlb-sea-hou-2026-09-24-hou']
B accounting cash 900.0  fees 14.47  net 313.53  residual 0.0
B basis VENUE_AUTHORITATIVE_SETTLEMENT_OF_THE_HELD_CONTRACT
B no_duplicates  positions 1  orders 1  outcomes 1
```

The production readback's A2b line is **post-restart liveness only** — the
process that ran the cycle carries this release's SHA. It is not inventory
recovery, because this lane holds no positions for a restart to recover. The
workflow prose now says exactly that instead of claiming recovery.

### I.3 Three defects found by exercising the lifecycle, not by reading pass/fail

1. **An autonomous entry was counted twice on every reload.**
   `bettor_mgmt_lifecycle.Managed.__init__` booked RN1's seed through
   `Portfolio.buy`, and `reload_managed` then replayed the fills. For an RN1
   position that is correct — RN1's entry is not our execution. For this
   lane's own entry it is a double count, because the order and the fill
   **are** in our ledger: 900 held read back as 1800. Fixed with
   `assign_seed`, resolved from `source_trade_id`. Pinned by 5 tests in
   `test_an_autonomous_entry_is_not_counted_twice.py`.
2. **A research-lane position could be entered and managed but never
   settled.** `bettor_entry_settlement.OPENING_BY_PROVENANCE` was never
   extended with migration 122's fourth provenance, so every
   `UNCALIBRATED_RESEARCH_SHADOW` position settled as
   `OPENING_PROVENANCE_NOT_DECLARED_FOR_THIS_POSITION` — enterable,
   manageable, then carried as open inventory forever with no reconciled
   accounting. Fixed.
3. **A fixture defect that hid defect 2.** The test `_seed` re-applied
   migration 117 (the 3-value provenance constraint) without 122, reverting
   the schema and making the research lane untestable. Production was
   unaffected: the runner applies 122 after 117.

---

## II · Current-market shadow operation — GENUINE NO_TRADE

All figures below are read from run 71's own recap of the completed cycle at
2026-09-26T00:14:15Z on `bfa3c5f`. This is separate from Part I and from the
synthetic acceptance position, and neither substitutes for it.

### II.1 Research-mode state: armed, and actually used

```
research_shadow_uncalibrated  true
  why  the UNFUNDED research lane may create simulated inventory while the
       calibration stays explicitly unmeasured
ext_pinnacle_shadow           armed true   submits_orders false
calibration_rows 0            still_unmeasured true
waiver  authorised 2   APPLIED 2  (a waived gate, not merely an armed lane)
        gates ["MODEL_TRUST_DRIFT"]   refusals []
```

The calibration gate is shut on its own declared terms:
`EXTERNAL_SOURCE_CALIBRATION_V2`, verdict `INSUFFICIENT_EVIDENCE`,
`gate_opens false`, 2 resolved fixtures against 450 required, **shortfall
298**. The Brier ceiling of 0.24 is a declared policy threshold, not proof of
calibration.

### II.2 The measured universe

Cycle `1790381655.310432`, state LIVE, `markets_considered 236`; venue universe
by label MLB 105, Soccer 131.

| source | sport | venue open+fresh | provider events | with Pinnacle | mapped | evaluated | written |
|---|---|---|---|---|---|---|---|
| `soccer_epl` | soccer | 131 | 20 | 20 | **0** | 0 | 0 |
| `baseball_mlb` | baseball | 105 | 23 | 14 | 13 | 8 | 8 |
| `soccer_mexico_ligamx` | soccer | 131 | 9 | 9 | 3 | **0** | 0 |

Refusals: `soccer_epl {NO_VENUE_CONTRACT_FOR_EVENT: 20}`;
`baseball_mlb {NO_PINNACLE_ON_EVENT: 9, VENUE_MAPPING_AMBIGUOUS: 1}`;
`soccer_mexico_ligamx {NO_VENUE_CONTRACT_FOR_EVENT: 6}`. The venue error, in
its own words: *"the venue's own catalogue carries no contract for this
fixture and outcome. `markets.slug` is the GLOBAL id and the venue does not
accept it."*

### II.3 Candidate economics

25 candidates read, 0 admissible. Over the window: 142 evaluated, 0
admissible, 142 refused, and the stage attribution reconciles with the window.

```
first refusing stage   1_PROBABILITY 61   5_EXECUTION_ESTIMATE 76   7_RISK 5
reached_execution_estimate 5      walk_took_levels 5
negative_edge_WITH_a_walk     0
negative_edge_WITHOUT_a_walk 76   (top of book only)
never_priced_at_all          61
POSITIVE_EDGE                 5   edge_not_computed 61
execution_mode  CROSSING_THE_ASK_AT_THE_TAKER_FEE
excludes        ANY_PASSIVE_OR_MAKER_FILL_ASSUMPTION
```

The measured edge is a **crossing** edge: `p_pay − ask − taker fee`, against
observed depth. It is not the passive/maker strategy, which keeps its own
execution assumptions and is not mixed into these counts.

**Five candidates priced positively.** That corrects the earlier position that
nothing did. Both fully-evaluated ones, on the record:

| | `aec-mlb-tb-phi-2026-09-25` | `aec-mlb-cin-tor-2026-09-25` |
|---|---|---|
| selection | Philadelphia Phillies | Toronto Blue Jays |
| p (de-vigged) | 0.29656 | 0.72690 |
| quote age / books | 15.98 s / 5 | 21.65 s / 5 |
| ask | 0.275 | 0.315 |
| fee per contract | 0.01386 | 0.01500 |
| **edge per contract** | **+0.00771** | **+0.39690** |
| walk | 1 level, vwap 0.275, break-even 0.286564 | 1 level, vwap 0.315, break-even 0.716897 |
| notional | intended 1000 / executed 1000 / unfilled 0, FULLY_SUPPORTED | same |
| settlement | INCOMPATIBLE (IN_PLAY) | INCOMPATIBLE (IN_PLAY) |
| gates failed | `STALE_DATA`, `UNRESOLVED_SETTLEMENT_SEMANTICS` | `STALE_DATA`, `UNRESOLVED_SETTLEMENT_SEMANTICS` |
| rails failed | all five | all five |
| waiver | applied, `["MODEL_TRUST_DRIFT"]` | applied, `["MODEL_TRUST_DRIFT"]` |

### II.4 Why nothing was admitted — cause, classified

**Missing source evidence. Binding, and not repairable by our code.**
`UNRESOLVED_SETTLEMENT_SEMANTICS` is a gate the waiver never waives. Over the
window: **COMPATIBLE 0**, INCOMPATIBLE 116 rows / 12 slugs, UNKNOWN 26 rows /
10 slugs, all baseball, and the conflict is a single condition —
`POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED` × 116. Read live from the
venue's own prose (379–384 chars, 4 sentences, `truncated false`,
`conflicts []`): the venue STATES
`PAYS_THE_LAST_FAIR_MARKET_PRICE_OF_THE_CONTRACT_NOT_A_STAKE_RETURN`, while
Pinnacle's cited rule for the same condition returns stake. Those are
different payout events, so a de-vigged `P(team wins | action)` does not price
this contract. What is missing is exactly two things we do not hold:
`P(abandoned and never completed)` and `E[last fair market price | that
condition]`. Neither may be invented, and the valuation bound without them is
`V ∈ [0,1]` — vacuous.

**Implementation defect.** Two were found and fixed (I.3). Two more are named
and unbuilt (items 5 and 6 below), plus the Liga MX alias defect (item 2),
which is the actual cause of the soccer mapping failure.

**Measured economics.** Not the cause for these 5, and not manufactured. It
*is* the honest cause for 76 of the 142: the observed best ask gave negative
edge before any walk. Reported as such, and no further.

One more real refusal on the same 5, which is ours: all five exposure rails
failed. The lane proposed a $1000 notional — 3636.36 and 3174.60 contracts,
$1042.05 and $2275.86 exposure — against rails scaled for the funded pilot's
$5.00-per-lifecycle limit. Even with settlement resolved, a proposal sized
that way is refused. That is a shadow-lane sizing/rail configuration gap, open
below as item 7, and deliberately not changed here.

### II.5 Autonomous inventory and P&L

```
§4 what this lane HOLDS, by provenance        positions 0
§5 reconciled P&L   mark_basis EXECUTABLE_EXIT_NET_OF_FEES_ON_OBSERVED_DEPTH
   historical        realised -3033.32  fees 11.05  marked 0 / unmarked 0
   prospective       realised -          fees -      marked 0 / unmarked 4
   autonomous_entry  realised -          fees -      marked 0 / unmarked 0
```

The autonomous lane's P&L is empty because it holds nothing. §6's recurring
management figures — `positions 4`, `with_observed_runtime_decision 0`,
`by_decision_basis {BACKDATED_TO_AVAILABILITY_UNAUDITED: 4}`, newest
2026-09-23T22:17:40Z — are **RN1's legacy set**. They are not evidence about
this lane, and not evidence about Houston.

The settlement sweep examined 1, settled 0, unresolved 1: the synthetic
Houston acceptance position
`RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1:ACCEPTANCE_SHADOW_MANAGER_DEMO_V1:-144218439`
on `aec-mlb-hou-ath-2026-09-25`, status `VENUE_HAS_NOT_SETTLED_THIS_CONTRACT`,
venue `PENDING/NOT_RESOLVED:PENDING`. It remains synthetic, modelled and
unfunded, and it was not reseeded.

### II.6 Soccer — a bounded finding, with one correction

The venue's own board reports 1400 events (1002 under `soccer`); the read is
capped at 400, of which 202 fall in the `soccer` bucket. Within that window:

- **Real competitions are present.** `lmx` is the real Liga MX — 2 events,
  e.g. `lmx-aft-cmf-2026-09-25` "Atlante FC vs. CF Monterrey". Also `arg2` 9,
  `brb` 4, `cnl` 3, `nwsl` 3, `uslc` 3, `lco` 2, `lexp` 1, `par2` 1, `afcq` 1.
- **Simulated competitions dominate it**: `ebfwca` 32, `ebfsa` 30, `ebfwcb`
  30, `ebfcwc` 10 — eBattles, whose own labels say so ("Will Man City defeat
  Boca Juniors in the eBattles") while carrying real club names. A simulated
  competition is not its real namesake, and the league token is the only thing
  that separates them.
- **Correction to an earlier verdict.** `lmx-aft-cmf-2026-09-25` is the same
  fixture run 67 refused as the global slug `mex-atla-mon1-2026-09-25-mon1`
  with `priced_outcome "Atlante FC"`. So for Liga MX the cause is **our
  alias/ingestion defect** — the venue's token is `lmx` while `markets.slug`
  carries the global `mex-` id the venue rejects — **not** missing venue
  coverage. My earlier "missing venue coverage" reading was wrong.
- **EPL is unresolved, not absent.** No `epl` token appears in the 400-event
  window. `soccer_epl`'s 20 refusals are all `NO_VENUE_CONTRACT_FOR_EVENT`,
  which is consistent with either absence or a window artefact.
- **The payout event, where the board was actually read.** Each of the three
  sampled `ebfcwc` events lists exactly three sides on the `dh1` full match —
  `-<home>`, `-draw`, `-<away>`. That is a three-way money line sold as three
  binaries. **Binary YES/NO packaging does not disqualify a contract**; I
  previously said it did, and that was wrong. What disqualifies a contract is
  the event it pays on — a half, a period, an inning, an exact score, a
  player.
- **Soccer therefore remains unevaluated**, for two separate reasons, and
  neither is "incompatible": mapping (0 of 20 EPL and 0 evaluated of 3 mapped
  Liga MX) and **no captured soccer settlement rules at all** — `BOOK_TERMS`
  is keyed only on `('baseball','h2h','PRE_GAME'|'IN_PLAY')`, so every soccer
  candidate is UNKNOWN by construction. Missing prose stays UNKNOWN; it never
  becomes compatible.
- One read-route imprecision worth naming: the desk's `soccer` bucket also
  contains non-soccer tokens (`acb`, `bbl`, `bsl`, `lnbp`, `ncaams`, `ncaaws`,
  `khl`, `f1`, `pga`, `boxing`). That is the classifier in the catalogue read,
  not in the entry lane.

---

## III · Capital readiness — NOT QUALIFIED

Nothing in Parts I or II implies this verdict is close.

- Funded trading and funded submission are **disabled**. `submits_orders
  false`. No order has been submitted to any venue. The funded caps —
  `MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE $5.00`, `MAX_SESSION_CUMULATIVE_SPEND
  $100.00`, `MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES 1` — remain unreached.
- The accounting-uncertain account stays paused. No pilot account has been
  identified or authorized, and none was switched.
- The calibration gate is shut on its own terms: 2 resolved fixtures of 450
  required, shortfall 298, `INSUFFICIENT_EVIDENCE`, `gate_opens false`.
- **There is no shadow track record to qualify on.** The autonomous lane holds
  0 positions and its P&L is empty. Part I proves the software runs; that is
  not an economic result.
- The Pinnacle input remains an external bookmaker's de-vigged price. It is
  not an internal or validated settlement model, and is not described as one.

---

## Open items, with owners and next actions

| # | item | status | owner | next action |
|---|---|---|---|---|
| 1 | Payout-rule conflict on `POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED` (116 rows, 12 slugs) | **missing source evidence — binding** | source evidence | obtain, from evidence, `P(abandoned and never completed)` and `E[last fair market price \| that condition]`, or find markets whose stated rules match the source's. Not inventable; the bound without them is vacuous. |
| 2 | Liga MX alias defect — venue token `lmx`, our slug carries the global `mex-` id | implementation defect, unrepaired | ingestion / mapping | add the venue-token alias to `bettor_venue_mapping` / `premap.resolve` so `lmx-aft-cmf-2026-09-25` resolves. It will still be UNKNOWN until item 3. |
| 3 | No soccer settlement rules captured; `BOOK_TERMS` is baseball/h2h only | missing evidence | settlement terms | capture Pinnacle's soccer h2h rules per condition and add the soccer key. Until then every soccer candidate is UNKNOWN by design. |
| 4 | EPL coverage on the venue | **unresolved** | catalogue read | page the full 1400-event board (the read caps at 400) and look for an `epl` token before concluding either way. |
| 5 | `copy_sports.market_type_of` returns on the kind prefix alone for the venue grammar and never inspects the suffix | named guard, unbuilt | classifier | extend the suffix analysis (currently kindless-feed-only) to the venue grammar, so double chance, exact score and segments cannot classify as a money line. |
| 6 | `bettor_venue_mapping` has no double-chance and no competition-alias check | named guard, unbuilt | mapping | add both, so a simulated competition can never bind a real-league probability. |
| 7 | Shadow sizing proposes a $1000 notional against funded-scale rails, so all five exposure rails fail on every candidate | open | risk / sizing | give the shadow lane its own rail scale, or size within the funded rails, so a settlement-clean candidate is not refused for an unrelated reason. |
| 8 | The `ent2` "32 failed" observation | **UNRESOLVED — see below** | — | — |

### The test discrepancy, preserved as unresolved

**Established.** On the current tree the three lifecycle files pass 21/21 on a
freshly migrated database. `tests/test_mirror_live_worker.py`, run whole-file,
produces an **identical 8-failure set** at `1d4dc37` (before any of these
changes) and at `bfa3c5f` — same test ids, both trees, same whole-file run. So
those 8 are pre-existing and not caused by the change.

**Not established.** Why the `ent2` run showed 32 failures. I attributed that
to pollution from my own diagnostic scripts and I did not prove it. The
originally-named test,
`test_mirror_live_worker::test_w2_one_row_per_transition_and_a_restamp_after_900_s`,
now passes in a whole-file run on both trees — which still does not prove the
change was uninvolved in whatever `ent2` showed. The item stays open.

**Separately pre-existing and unrelated.** `031_us_premap_signed.sql` and
`055_us_premap_team.sql` both fail on a fresh database with `relation
"us_premap" does not exist` — reproduced again while preparing this record.

---

## Operating controls

| control | how it is set |
|---|---|
| entry lane | env `EXT_PINNACLE_SHADOW` **and** row `ext_pinnacle_shadow` |
| manager | env `RN1X_SHADOW` **and** row `rn1x_shadow` |
| research waiver | row `research_shadow_uncalibrated`, version `UNFUNDED_RESEARCH_SHADOW_V1`; waives `MODEL_TRUST_DRIFT` only |
| cadence | `CYCLE_S 900`, `MAX_PER_CYCLE 40`, `TICK_S 20`, `IDLE_S 120` |
| freshness | `PINNACLE_MAX_AGE_S 30` (the source's own rule) |
| release | `render-ops.yml` → `deploy-api-commit`, API only, by commit id; worker pinned at `f5d1c05` |
| trace | `/api/command/rn1x/trace/<position_id>` on the API host |

Both env flag and database row are required to arm anything; either one alone
leaves the lane off.
