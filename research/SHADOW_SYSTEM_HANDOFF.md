# Shadow system — management handoff

One operating system, two acceptance verdicts, kept apart. Written
2026-09-25 from `command-verify` run 69 (36190536936) with the
current-market census from run 68 (36188978059). Every figure is read from a
run; none is derived.

> **Funded trading and funded submission are DISABLED. Capital qualification
> is a separate verdict and nothing here implies it.** The system writes
> modelled orders and simulated fills only; migration 100 CHECKs
> `is_modelled` true on every `rn1x` order and fill, and no order has been
> submitted to any venue.

---

## 0 · Verified live SHA

Read from the **running process's own identity**, not from a deploy log:

```
entry loop writer {"pid": 1,
                   "build": "52a7104e0bc014d965bb138d7ddfa5243fc2a8cf",
                   "module": "sportsassets.workers.ext_pinnacle_loop",
                   "source_sha256_12": "e4de0d0c56ef"}
state LIVE   at 1790370579.12  =  2026-09-25T21:09:39Z
```

* **Live API build: `52a7104`** (released API-only, by commit id, no push).
* The worker service stays on `f5d1c05`, deliberately untouched — both
  services track the same auto-deploy branch, so a push there would restart
  the observation collector mid-window.
* Commits after `52a7104` on `claude/command-center` (`dc6e74a`, `6a13e17`,
  `c007cb5`) are **workflow and documentation only** and are not deployed.
  Nothing in them changes engine behaviour.

**Dashboard:** `https://command.bettortoken.com` — the RN1X shadow surface,
behind the COMMAND session. Deep link for the demonstrated position is in §A.

---

## A · Software capability — the lifecycle runs end to end

One position, found by the property that matters (orders **and** fills
**and** a settled outcome row) across the newest 60, not hardcoded:

```
subject  RN1X_MGMT_PAIR091_STOP16_V1:MANAGEMENT_PAIR_091_STOP_16_V1:30070
trace    …/api/command/rn1x/trace/RN1X_MGMT_PAIR091_STOP16_V1:MANAGEMENT_PAIR_091_STOP_16_V1:30070

1 ENTRY        policy MANAGEMENT_PAIR_091_STOP_16_V1   entry_kind INITIAL_ENTRY
2 ORDERS       6   BUY/EXPIRED @0.43 x6.53 …  last one filled 2.09523775
                   basis PRINT_THROUGH_WITH_QUEUE_SHARE_V1
3 FILLS        1   @0.42 x2.095238  fee 0.04  queue_share 0.25
4 MANAGEMENT   decisions 23   distinct_instants 13   newest 2026-07-22T20:27:59Z
5 EXIT/SETTLE  settled_at 2026-07-22T22:19:45Z  residual 0  unpaired 0
                   basis OBSERVED_PAYOUT_SCORING_ONLY
6 ACCOUNTING   realized_cash -1.83   fees 0.04   net -1.83
```

Every stage is traversed: entry selection, six modelled orders, a **partial**
simulated fill (2.095 of 6.53), 23 recurring management decisions at 13
distinct instants, settlement to zero residual, reconciled accounting, and a
published trace.

**The labels, in the route's own words:** *"Every fill above is MODELLED. It
was licensed by an OBSERVED print by someone else, allocated at a declared
queue share. P_FILL remains NOT_IDENTIFIED: none of this is evidence that our
order would have been filled."*

**This proves capability. It says nothing about profitability** — net is
−1.83 — **nothing about current markets, and nothing about capital.**

### A2 · Duplicate protection, demonstrated on a live repeat

A second `rn1x-acceptance-position` call returned the existing position
unchanged:

> *"an acceptance position already exists under this experiment and policy,
> so it was ADOPTED and returned unchanged. No row was written and no
> inventory was added: re-seeding is a lookup, not a second entry."*

`submits_orders false  funded false`. Migration 114's unique index is the
database-level backstop, and `bettor_entry_inventory.R_ALREADY_HELD`
(`EXPOSURE_ALREADY_HELD_BY_THIS_LANE`) is the entry lane's own guard.

### A2b · Restart recovery

The API was restarted by this release. After it, the entry loop retook its
writer lock and reported `state LIVE` at 21:09:39Z under build `52a7104`, and
the manager continued on the **same** persisted inventory — Houston's
decision sequence ran on to D0165/D0166 rather than starting a new set.

### Limitations of this demonstration, stated

* The ENTRY line is thin — `provenance`, `qty`, `filled`, `residual` and
  `cost_basis_usd` all read `-`. That position was written in July, before
  those columns existed on `rn1x_positions`. The quantities are visible on
  its orders and fills, not on the position row.
* `residual_settled_usd`, `turnover_usd` and `committed_peak_usd` are null on
  its outcome row: that lane did not write them. The reconciled figures that
  **are** present are `realized_cash_usd`, `fees_usd` and `net_usd`.
* Five of six orders EXPIRED unfilled. That is the fill model refusing, not a
  failure — and it is why `P_FILL` stays `NOT_IDENTIFIED`.

---

## B · Current-market operation — a genuine NO_TRADE

Scheduled research lane, 6-hour window, one live cycle. Kept entirely
separate from §A and from the synthetic acceptance positions.

```
observed universe    296 open venue markets in supported sports
                     by label {MLB 66, Soccer 230}
evaluated            145        admissible 0        refused 145
  1_PROBABILITY       51
  4_SETTLEMENT_SCOPE  54
  5_EXECUTION_ESTIMATE 40
POSITIVE_EDGE          0        edge_not_computed 51
negative (no walk)    94        reached_execution_estimate 0
execution_mode       CROSSING_THE_ASK_AT_THE_TAKER_FEE
excludes             ANY_PASSIVE_OR_MAKER_FILL_ASSUMPTION

settlement coverage  COMPATIBLE 0
                     UNKNOWN      102 / 10 slugs   all baseball
                     INCOMPATIBLE  43 /  9 slugs   all baseball
                     conflicting {"POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED": 43}

autonomous_entry     positions 0      P&L empty
```

**Autonomous shadow inventory: 0 positions. P&L: empty.** No position was
created because no candidate passed, and none was forced. The maker/passive
experiment keeps its own fill assumptions; none of its numbers appear here.

The funnel, per supported sport:

| provider sport | venue open+fresh | events | with Pinnacle h2h | mapped | evaluated |
|---|---|---|---|---|---|
| `soccer_epl` | 230 | 20 | 20 | **0** | 0 |
| `soccer_mexico_ligamx` | 230 | 9 | 9 | 3 | **0** |
| `baseball_mlb` | 66 | 23 | 16 | 10 | 6 |

---

## Valuation verdict — the three causes, separated

The mandate: repair an implementation defect, name missing source evidence
precisely, do not manufacture measured economics. All three are present, on
different markets.

### 1 · baseball/h2h → MEASURED ECONOMICS, plus an established conflict

* **Economics.** `POSITIVE_EDGE 0` across 94 priced rows, against the
  observed best ask with the taker fee. For a long entry the best ask is the
  cheapest price, so non-positive edge there rules out any size. Not a
  defect, not missing evidence. **Nothing to repair and nothing to
  manufacture.**
* **Payoff.** 43 rows over 9 slugs are **INCOMPATIBLE** on
  `POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED`: the bookmaker returns the
  stake, the venue pays the last fair market price. Stated by both sides;
  different payout rules, **not waivable**. 102 rows are UNKNOWN because the
  venue's prose is silent on six of seven conditions — silence is not
  agreement.

### 2 · soccer/h2h identity → IMPLEMENTATION DEFECT, ours

The venue's own board, read through the route the desk uses:

```
counts {"soccer": 997, "nfl": 137, "tennis": 105, "esports": 81,
        "mlb": 44, "nhl": 32, "wnba": 4, "all": 1400}
soccer  events 316  moneyline_family_sides 838  e.g. atc-ebfpl-ars-che-2026-09-25-dh2-ars
mlb     events  15  moneyline_family_sides  45  e.g. atc-mlb-atl-mia-2026-09-25-i8-draw
tennis  events  45  moneyline_family_sides  90  e.g. aec-atp-danmed-valroy-2026-09-23
```

**The venue sells 997 soccer events with 838 sides under its own money-line
prefixes, and our lane reached none of them.** That settles the attribution:
this is **our** ingestion/mapping gap, not missing venue coverage. Named
precisely:

* `markets.slug` for soccer holds **global-CLOB** ids, and the premap refuses
  with the venue's own words — *"`markets.slug` is the GLOBAL id and the venue
  does not accept it."*
* The venue's EPL league token is **`ebfpl`**, not `epl`. Our 20 EPL events
  refused with `NO_VENUE_CONTRACT_FOR_EVENT` 20/20.
* One of the three Liga MX rows that did match was
  `mex-tij-atl-2026-09-25-exact-score-0-3` — an **exact-score** market, not a
  money line, matched on team names.

**What this does NOT establish, and I am not claiming it.** The `aec`/`atc`
test counts the money-line *family*. The soccer example is a `dh2`
(double-chance/handicap) variant and the MLB example is `i8-draw` (an inning
segment), so **"a full-match 3-way soccer money line exists" is not yet
established** — only that venue-native soccer contracts under money-line
prefixes exist in quantity, which is already enough to place the defect on
our side. Which soccer slug shape is the full-match 3-way line is the next
concrete read.

### 3 · soccer/h2h payout rules → MISSING SOURCE EVIDENCE, named

* **Our side:** `BOOK_TERMS` has no soccer entry. Pinnacle's soccer rules are
  now captured verbatim
  (`backend/tests/fixtures/pinnacle_soccer_rules_2026_09_25.json`) but
  deliberately not admitted, because with zero soccer candidates it would
  change no verdict.
* **Venue side:** the venue's own soccer prose has **never been read** — no
  soccer candidate has ever existed to read it for.
* Therefore soccer settlement is **UNKNOWN on both sides**, and UNKNOWN is
  not compatible. Three specific unknowns, from the fixture: whether the
  venue contract is 2-way or 3-way; whether it grades on 90 minutes or on the
  match result (Pinnacle excludes extra time and penalties, so a venue paying
  "the winner of the match" disagrees on exactly that branch); and the
  abandonment branch.

---

## Supported scope, as the code defines it

| | |
|---|---|
| Probability source | Pinnacle, de-vigged (`PINNACLE_DEVIG_V1`) via the-odds-api |
| Source-supported pairs | `baseball/h2h` (2 outcomes), `soccer/h2h` (3 outcomes) |
| Provider sports polled | `soccer_epl`, `soccer_mexico_ligamx`, `baseball_mlb` |
| Venue labels consumed | `MLB`, `Soccer` |
| Bookmaker rules captured | `baseball/h2h` PRE_GAME and IN_PLAY, 7 conditions, each with its cited quote |
| Captured scope | `baseball/h2h` REGULAR_SEASON + STANDARD_NINE_INNING **only** |
| Venue rules | read live per candidate from the listing's `description` |
| Freshness | `PINNACLE_MAX_AGE_S = 30.0` |

## Operating controls

Every stop is a **database write, not a deploy**.

| control | where | effect |
|---|---|---|
| `EXT_PINNACLE_SHADOW` env | API service | `off`/`0`/`false` disables the entry lane |
| `ingestion_state.ext_pinnacle_shadow` | DB row | must be `true`; absent or unreadable = stopped |
| `RN1X_SHADOW` env | API service | pre-check for the manager |
| `ingestion_state.rn1x_shadow` | DB row | must be `true`; the authority |
| `ingestion_state.research_shadow_uncalibrated` | DB row | waives **exactly** `MODEL_TRUST_DRIFT`; never `STALE_DATA`, `OUT_OF_DISTRIBUTION`, `UNRESOLVED_SETTLEMENT_SEMANTICS` |
| cadence | code | entry `CYCLE_S 900`, `MAX_PER_CYCLE 40`; manager `TICK_S 20`, `IDLE_S 120` |
| funded caps | code | `MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE 5.00`, `MAX_SESSION_CUMULATIVE_SPEND 100.00`, `MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES 1` — **unreached; no funded submission path is armed** |

---

## Remaining limitations, owners, next actions

| # | Limitation | Owner | Next action |
|---|---|---|---|
| 1 | Soccer never reaches a valuation: venue-native soccer slugs are absent from `markets`; the venue's league token is `ebfpl`, ours is `epl` | engineering (ingestion) | Read one soccer event's full market board and identify the full-match 3-way money-line slug shape; then populate `markets` with venue-native soccer slugs, or map to them at premap. One read, then a bounded ingestion change. |
| 2 | Venue soccer settlement prose unread; `BOOK_TERMS` has no soccer entry | engineering (settlement) | Blocked by #1 by design. Once one soccer candidate exists, the prose read and the comparison run on the existing machinery; the bookmaker half is already captured. |
| 3 | Baseball is INCOMPATIBLE on the price-settled branch | **not repairable in code** | Needs a reference source whose postponement rule matches the venue, or a market with no price-settled branch. The refusal stays. |
| 4 | Baseball shows no positive edge at any size | **measured; do not repair** | Re-measure only after #1 changes the candidate set. Do not relax a gate to create activity. |
| 5 | `P_FILL` is `NOT_IDENTIFIED`; simulated fills are an execution assumption | research | Unchanged and correctly labelled. A funded fill-rate measurement is a separate, funded decision. |
| 6 | The controlled-lifecycle position predates `provenance`/`qty` columns and its outcome row lacks `turnover`/`committed_peak` | engineering (reporting) | Prefer a newer position once one traverses every stage; the search already picks by property, so this resolves itself. |
| 7 | Acceptance for recurring management is not met: 0 complete decisions at 0 distinct instants under the acceptance policy | research | Blocked on #3 — `SETTLEMENT_COMPATIBILITY_ESTABLISHED` cannot pass while the conflict stands. D0165/D0166 are conditional evidence that management runs, not acceptance. |
| 8 | `render-ops.yml` is 422 bytes from the size ceiling | engineering (tooling) | Split it before the next edit. Pre-existing; unrelated to this work. |

**Capital qualification is untouched by all of the above.** Shadow-system
completion is a software verdict. Nothing here is a profitability finding,
and the funded boundary was not approached.
