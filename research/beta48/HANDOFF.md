# HANDOFF — the autonomous shadow system, as deployed

2026-09-24. Shadow only. No funded orders were placed and none can be.
The damaged account `acct_fc2d773a2afa4851` remains paused and
ACCOUNTING_UNCERTAIN; nothing added here reads or writes it. The protected
collector was not interrupted (see §6). No frozen threshold was changed, no
budget or request limit was raised, no account was reset.

**Profitability is not claimed, measured, or implied anywhere in this
document.** Deployment is not evidence of edge; a passing test is not
evidence of edge; a fitted model beating its own baseline in-sample is not
evidence of edge. §5 states exactly what has and has not been measured.

---

## 1 · Deployed identities

| | |
|---|---|
| repository | `matthewtaylor141-lab/SportsAssets` |
| default branch | `claude/session-njaewf` (this is where `workflow_dispatch` registration lives) |
| work branch | `claude/command-center` |
| API service | `sportsassets-api` — `srv-d9gcv6urnols73ce6er0` |
| worker service | `sportsassets-workers` — **untouched**, live deploy `7f76fd9` since 2026-09-23T10:26:55Z, verified unchanged before and after every API deploy |
| database | `sportsassets-db` — `dpg-d9gcudurnols73ce4avg-a` |
| odds credential | `EDGE_ODDS_API_KEY` on `sportsassets-api`, provisioned 2026-09-24T01:01:04Z (Render PUT **HTTP 200**) |

**Migrations added:** `104_rn1x_clock_integrity.sql`,
`105_external_valuations_one_per_observation.sql`.

## 2 · Operating processes

Five loops run inside the API. Each has **two independent stops**: an
environment flag (a deploy) and a control row in `ingestion_state` (prompt,
no deploy). Absence of the row is never permission.

| loop | env flag | control row | what it does |
|---|---|---|---|
| `workers/rn1x_shadow` | `RN1X_SHADOW` | `rn1x_shadow` | the RN1-seeded management experiment, two lanes |
| `workers/rn1x_learn_loop` | `RN1X_LEARN` | `rn1x_learn` | **policy comparator.** Fits nothing |
| `workers/ext_pinnacle_loop` | `EXT_PINNACLE_SHADOW` | `ext_pinnacle_shadow` | external bookmaker valuation → gate → persist |
| `workers/rn1x_model_loop` | `RN1X_MODEL_FIT` | `rn1x_model_fit` | **actual fitting**: prepare → fit → predict → join → evaluate |
| `bettor_desk_loop` | `BETTOR_DESK_LOOP` | — | pre-existing |

Stop any of them with
`POST /api/admin/ext-pinnacle-shadow/off` or
`POST /api/admin/rn1x-model-fit/off` (both `X-Admin-Token`), or by setting
the control row false. Neither requires a deploy.

## 3 · Management click paths

All behind the COMMAND session. **Twelve statuses**, each naming what it is
and why, with the badge vocabulary
`LIVE / ARMED / STOPPED / BLOCKED / UNAVAILABLE / OK / EMPTY / CHECK`:

| route | answers |
|---|---|
| `/api/command/rn1x/statuses` | all eleven tiles: operating state and source freshness |
| `/api/command/rn1x/external/probe` | can this process retrieve odds; coverage; quote ages; credit balance; **and the second-half blocker, measured** |
| `/api/command/rn1x/external/census` | what the valuation engine decided and why it refused |
| `/api/command/rn1x/external/trace/{id}` | ONE valuation: raw odds, both timestamps, mapping, de-vig method, probability, executable price, cost, estimated edge, decision, outcome |
| `/api/command/rn1x/clock-audit` | which positions carry an OBSERVED decision instant and which were backdated |
| `/api/command/rn1x/positions` · `/trace/{id}` | one position end to end, with `clock_semantics` |
| `statuses.order_book_state` | resting orders, partial fills, cancellations, remaining size — **by lifecycle state** |
| `statuses.shadow_pnl` | realised cash, fees, residual and unpaired quantity per lane |
| `statuses.model_fitting` | target, predictions recorded, outcomes joined, last cycle, and that it promotes nothing |
| `statuses.writer_ownership` | which loops hold their writer lock, read from `pg_locks`, and that an advisory lock binds only the loops that ask |

The three lanes stay visibly separate by experiment id:
`RN1X_MGMT_PAIR091_STOP16_V1` (historical replay),
`..._PROSPECTIVE` (prospective RN1 management),
`EXT_PINNACLE_DEVIG_V1_SHADOW` (independent external-valued shadow).

## 4 · Observed evidence, with timestamps

**Authenticated production read, run 21, job 107462533297,
2026-09-24T02:03–02:08Z, against API deploy `e0929fd`:**

```
historical_replay           LIVE          prospective_rn1_management  LIVE
independent_ev_entries      BLOCKED       pairing                     LIVE
second_half_loss_exit       UNAVAILABLE   accounting_health           OK
learning_evaluation         LIVE          external_valuation          ARMED
order_book_state            LIVE    386 modelled orders, both lanes
shadow_pnl                  LIVE    143 settled positions
model_fitting               LIVE    316 predictions, 0 joined
```

**The odds credential works in the running process** (02:03:15Z): HTTP 200,
`retrieved true`, 20 of 20 EPL events carrying Pinnacle h2h, quote age
**28.8 s** on all 20, credits 8,075,605 used / 6,924,395 remaining,
`key_value_returned false`.

**The external loop ran a real cycle** (02:08:18Z) — the first one to get
past candidate selection, because the sport-label defect (§8) was fixed:
`state LIVE`, **4,000 open venue markets considered**, `elapsed_s 3.67`,
`evaluated 0`, `written 0`, and every one of the 46 events accounted for
by name:

```
NO_VENUE_CONTRACT_FOR_EVENT       41
NO_PINNACLE_ON_EVENT               2
NO_CONTEMPORANEOUS_VENUE_QUOTE     2   <- reached only AFTER a successful mapping
VENUE_MAPPING_AMBIGUOUS            1
```

That third line is the first observed production evidence that the
conservative mapper **can** match a Pinnacle fixture to an open venue
contract: two did, and then refused at the venue read. `VENUE_ASK_HAS_NO_DEPTH`
and `VENUE_QUOTE_STALE` did not fire, so the cause is one of three — no
slug on the market row, the book read raising, or a venue error response.
Those three shared one counter; they are now three named counters.

**The clock audit** (02:05:46Z): **147 positions, 0 able to support a
prospective claim, 130 labelled `BACKDATED_TO_AVAILABILITY_UNAUDITED`**
(126 historical + all 4 prospective), 17 `REPLAY_AT_AVAILABILITY`. The
four pre-existing "prospective" positions are preserved and reclassified,
never rewritten — their receipt instants were overwritten before migration
104 and cannot be recovered, so inventing one would be the original defect.

**The model loop fitted on production data** (02:08:18Z): 40,000 source
rows → 6,265 built, 5,966 closed and fitted, base rate 0.31646, 1,888
positives, `dataset_sha` `68ac9f00dc68c2d2`, `model_version` `2.68ac9f00`,
`unmatched_to_source_fill 0`. In-sample log loss **0.5670** against a
**0.6242** baseline — labelled `IS_IN_SAMPLE`, **a fit diagnostic and not
performance**. Stage 5 refuses by name:
`TOO_FEW_JOINED_PREDICTIONS_TO_EVALUATE`, floor 50, joined 0.

**One cycle did not double-count the previous one** (02:08:18Z): of 299
open rows, the cycle **wrote 157 and recognised 142 as already present**.
That is the "subsequent cycle processing without duplicate accounting"
demonstration, taken from production rather than a fixture.

**Controlled acceptance**, `tests/test_controlled_acceptance.py`, 17 tests,
all through the deployed machinery: BUY through the real gate; pairing at
the frozen 0.91 combined including fees; the loss trigger and the realised
loss as two numbers; no fill; partial fill leaving the remainder working;
one print not consumed twice; a CANCEL_PENDING order still filling; one
order at a time; **paired inventory is not cash until settlement**;
settling twice not paying twice; restart recovery on a fresh connection
with no duplication; atomic persistence.

## 5 · What is measured, and what is not

**Two measurements that decide open questions.**

*The second-half exit cannot be closed with existing access.* The
provider's scores endpoint answered HTTP 200 with `progress_fields []` and
`carries_observed_period false`. Halfway rules are written for soccer,
basketball and football; `PROGRESS_FEED_CONNECTED` is empty. The exact
missing capability: **a timestamped period/quarter/inning/clock
observation, not derived from a scheduled start.** This needs a different
provider — see §7.

*The bulk endpoint's freshness straddles the 30 s rule — it is not a
permanent blocker.* Quote age measured **32.7 s** (01:06:57Z, refused),
**36.2 s** (01:32:20Z, refused), **28.8 s** (02:03:15Z, n=20, inside the
rule), with LigaMX at 29.8 s earlier. The limit was **not changed**:
raising it would manufacture activity by loosening a risk parameter. The
02:08Z cycle shows odds freshness was not what stopped it — the mapping
and the venue read were.

**Not established, and not to be read as established:**

- **No opportunity has been found, and none ruled out.** `external_valuation`
  has evaluated 0 — and as of 02:08Z the reason is measured rather than
  assumed: of 46 events, 41 have no venue contract, 2 no Pinnacle quote, 1
  is ambiguous, and 2 mapped and then failed the venue read. Nothing
  reached scoring, so the settlement-rule refusal has not even been
  exercised on live data yet.
- **No calibration, no net shadow return, no execution sensitivity.** All
  need outcomes joined to predictions recorded beforehand. At 02:08:18Z the
  ledger held **316 predictions and 0 joined outcomes**, and the evaluation
  stage refused by name rather than reporting a number.
- **`P_FILL` remains `NOT_IDENTIFIED`.** Every fill anywhere is modelled,
  and the depth read is *displayed* depth, explicitly not a queue position.
- **Unrealised P&L is `NOT_IDENTIFIED`** by choice: a midpoint is where
  nobody transacted, so open inventory is carried at cost.
- **The venue's settlement rule is not in our data at all.** Draw,
  overtime, push and void are reported separately; soccer leaves three
  unestablished, baseball two.
- **This source has never been measured against the venue price.** The
  internal challenger measured *worse* (Δ log loss −0.00926); nothing here
  revisits that.

## 6 · Preserved

- **The protected collector was not interrupted by this work.** Six API
  deploys produced no `PROCESS_REPLACED` in `bettor_incentive_journal`, so
  the collector is not in that process. The one 22.665 s PROCESS_REPLACED
  (2026-09-23T22:58:34Z) does not coincide with any deploy here and the
  worker service's live deploy is unchanged since 10:26:55Z. At 01:47:55Z
  collection was healthy: 12 of 12 markets receiving with depth, 34,045
  frames, all in window.
- **Funded execution is unavailable at the adapter, not by a database
  field.** `pmus.submit_fok`'s first executable statement is
  `execution_gate.authorize("submit", ...)`, read at the moment of
  submission, and denial **raises**. An undeclared lane is treated as
  `unknown` and still gets the copy controls. With no pool bound the gate
  builds an empty snapshot rather than short-circuiting to permission.
  `tests/test_ext_shadow_cannot_fund.py` verifies the first-statement
  property and the attribute set taken off `pmus` **with the AST**, because
  a comment would fool a grep either way. The
  `external_valuations.order_submitted` CHECK remains a backstop against a
  lying writer — which is all it ever was, and offering it as proof that no
  request could leave the process was wrong.
- Budgets, request limits, frozen thresholds, the paused account and the
  observation stop at 2026-09-24T04:00:00Z: all unchanged.

## 7 · Remaining limitations, and the smallest action needed

| # | limitation | smallest action from you |
|---|---|---|
| 1 | second-half loss exit is UNAVAILABLE | a feed carrying observed period/clock for soccer. The rule, the store, the staleness checks and the refusals are all built and exercised in controlled tests; only the observation is missing |
| 2 | external valuation has never reached scoring. At 02:08Z the binding refusals were the venue slate (41 of 46 events have no open venue contract) and the venue read (2 of 46, after mapping succeeded) — **not** odds freshness | nothing from you for the slate: the venue simply does not list most EPL fixtures, and matching them loosely is the one thing the mapper exists to refuse. For the venue read, the three causes now have three separate counters and the next armed cycle will name which one; if it is a venue error response, that is an access question I will bring back with the exact message |
| 3 | venue settlement rule unattested | capture the venue's per-market rules text, or measure the rule from `resolved_prices` on fixtures decided after 90 minutes |
| 4 | no calibration or net return yet | time. Predictions must be recorded, then their horizons must close |
| 5 | `render-ops.yml` has 422 bytes of headroom against a 512,000-byte ceiling, already below this repository's own 32 KiB rule | it is the lever that operates Render and holds the trading kill switch. It needs splitting. I did not add to it |
| 6 | branch reconciliation | `claude/command-center` is ahead of the default branch. Everything is deployed by commit id, so nothing here depends on merging. Say the word and I will reconcile |
| 7 | pre-existing test debt: 449 failures at the pre-session commit and the **same 449** at HEAD, identical node ids (§8) | nothing from you. This work adds none of them; the earlier 545 reading was a load artifact and did not reproduce |

## 8 · Defects found by arming, and one discrepancy still open

**Three production defects, all mine, all found only once the loops were
armed and read back.** None was visible in a passing test:

1. **Prediction ordering.** The fills query took `ORDER BY detected_at ASC
   LIMIT 40000`, i.e. the *oldest* rows, so every row's horizon had closed,
   `n_open` was 0 and the PREDICT stage recorded nothing while reporting
   success. Fixed with a newest-first subquery re-sorted chronologically,
   and pinned by a test. Production now shows `n_open 299`.
2. **Two sport vocabularies.** The candidate-market query asked for
   `sport IN ('soccer','baseball')` while `markets.sport` is written by
   `sports.classify` as `'Soccer'` / `'MLB'`. It matched **zero** markets,
   and the cycle reported 44 mapping refusals that were not the mapper's
   fault. Fixed with an explicit label map plus a distinct refusal code for
   "no candidate markets at all". Production now considers **4,000** markets
   and produced the first successful mapping.
3. **The ledger's write count.** `record_prediction` used `execute` against
   `ON CONFLICT DO NOTHING`, so it reported attempts as writes — "recorded
   293" beside a table holding 159. Fixed with `fetchval ... RETURNING id`
   and separate `written` / `duplicate` counters, which is also what now
   evidences idempotency across cycles (157 written, 142 already present).

**The discrepancy resolved: there are no test regressions.** The full
suite, same command, same machine, three runs:

| run | failed | passed | skipped |
|---|---|---|---|
| pre-session `fa544da` | 449 | 11,241 | 183 |
| HEAD, taken under concurrent load | 545 | 11,174 | 190 |
| HEAD, quiet machine | **449** | 11,293 | 196 |

The quiet HEAD run's failure set is **identical to the baseline's, node id
for node id: 0 new, 0 fixed.** The 545 was not reproducible. All 96 of its
extra failures pass when re-run on their own at HEAD, and 24 of them are
allocator and memory-census diagnostics that measure the host. That first
HEAD measurement was taken while a local Postgres cluster and repeated
production polls ran in the same container; the baseline was not.

I reported the 545 as a measured regression before re-running it, which
was the wrong order: one run of a load-sensitive suite is a reading, not a
finding. **The standing position is the one above — this work introduces
no test failures, and the 449 are pre-existing.**

## 9 · Run 22, 2026-09-24T02:28–02:33Z — the second production read

Against API deploy `098e94c`. Authenticated, twelve tiles.

**Single-writer ownership is now observed, not claimed:**

```
writer_ownership  OK   4 of 4 loops hold their own lock, one pid each
  workers/rn1x_shadow       pid 3374926  granted
  workers/rn1x_learn_loop   pid 3374928  granted
  workers/ext_pinnacle_loop pid 3374929  granted
  workers/rn1x_model_loop   pid 3374930  granted
  more_than_one_holder      []
```

**The split counter answered its own question on the first cycle.** What
was `NO_CONTEMPORANEOUS_VENUE_QUOTE 2` is now
`VENUE_BOOK_READ_RETURNED_ERROR 2`: the two Pinnacle fixtures that mapped
exactly to an open venue contract were refused because **the venue
answered the book read with an error**. Not staleness, not missing depth,
not a missing slug — each of those has its own counter and none fired. The
loop now also keeps the venue's own message, so the next read says whether
that is an entitlement, a closed market or a rate limit. That is the one
thing standing between this pipeline and its first scored valuation.

```
markets 4000   elapsed_s 2.1   evaluated 0   written 0
NO_VENUE_CONTRACT_FOR_EVENT     39
NO_PINNACLE_ON_EVENT             2
VENUE_BOOK_READ_RETURNED_ERROR   2
VENUE_MAPPING_AMBIGUOUS          1
```

**The ledger grew without double-counting, again.** 316 predictions at
02:08Z, **866 at 02:33Z**, 0 joined. The 02:33 cycle predicted 298 open
rows, wrote 168 and recognised 130 as already present. Fit n=5,841, base
rate 0.31827, in-sample log loss 0.5748 against 0.6256, `dataset_sha`
`803dd81b94b7fa75`, `model_version` `5.803dd81b`. Stage 5 still refuses:
`TOO_FEW_JOINED_PREDICTIONS_TO_EVALUATE`.

Order book 414 modelled orders; 154 settled positions; unrealised
`NOT_IDENTIFIED`. Clock audit: 158 positions, **0 able to support a
prospective claim**, 130 backdated, 28 `REPLAY_AT_AVAILABILITY`. The
second-half probe is unchanged: `progress_fields []`,
`carries_observed_period false`.

**Still not established:** no valuation has been scored, so no
probability, cost, edge or decision exists in production; no calibration;
no net return. Nothing here is a profitability claim.

## 10 · The six-item follow-up, and what each is resting on

### 10.1 The venue read (item 1)

The refusal is now answerable in one authenticated GET rather than a
15-minute cycle: `/external/probe` runs **the loop's own reader** against
three open markets and returns, per slug, the stage, the venue's own code,
an HTTP status when the client exposes one, and a sanitized detail. Every
free-text field passes a redactor that removes query strings, `key=`/
`secret=`/`signature=` pairs and token-shaped runs, because a diagnostic
nobody is allowed to show is not a diagnostic.

**A correction to my own claim.** I said the book error was "the one thing
standing between this pipeline and its first scored valuation". That was
wrong in an important way. The venue read is the next refusal; it is not
the last one. Downstream of a successful read, in order:

| # | requirement | state today |
|---|---|---|
| 1 | contemporaneous venue ask **and displayed depth** | blocked at the read |
| 2 | venue settlement rule agrees on draw / overtime / void | **NOT established.** `bettor_venue_settlement.ATTESTED` is empty; soccer leaves 3 unmet, baseball 2. These are handed to the engine as caller refusals and **veto admission** |
| 3 | per-outcome book count ≥ `MIN_OUTCOME_BOOKS` | unknown until a cycle gets there |
| 4 | fee from the production schedule, never defaulted | wired |
| 5 | net edge ≥ `MIN_NET_EDGE_PER_CONTRACT` (0.01) | unknown until 1–4 pass |
| 6 | `bettor_entry_gate.admit` | its own refusal list |

So the **first complete record will decide NO_TRADE**, and it will do so
on the settlement rules. That is still the deliverable item 1 asks for —
odds, mapping, contemporaneous price and depth, fees, and a decision — but
a BUY is not reachable until the venue's own market rules are attested.
Nothing here will be relaxed to produce one.

### 10.2 The prospective lane (item 2)

The tile badged LIVE whenever the lane held any position. Four positions
written before migration 104 — every one backdated and reclassified — made
that read as prospective evidence. It now separates them:

- `with_observed_runtime_decision` — positions whose `decision_basis` is
  `RUNTIME_WALL_CLOCK`, the only basis that supports a prospective claim;
- `legacy_backdated_and_reclassified` — everything else;
- `last_cycle_prospective` — the lane's own state, examined/written counts
  and refusals from its most recent pass, so "wrote nothing" carries a
  reason instead of being inferred from an absence of rows.

**Zero of the first is `CHECK`, however live the loop.** A running process
and prospective evidence are different claims and the badge no longer
stands in for the evidence.

### 10.3 Outcome joining (item 3)

The join has always been correct about censoring: `JOINABLE` selects only
predictions whose `predicted_at + horizon_s` has passed, and a closed
horizon with no complement fill is a genuine 0 — a missing observation is
never turned into a negative label. What was missing was the ability to
read "0 joined". The tile now reports earliest prediction, earliest
maturity, next maturity and **matured-but-not-joined**, which is the number
that separates waiting from a defect. Horizon is 3,600 s and the join runs
once per fitting cycle.

### 10.4 Event progress (item 4)

**No accessible source exists**, and this is now recorded as a search
rather than an assertion: the odds provider's scores endpoint answered 200
twice with no period field; the venue's `marketData` carries quotes,
ladders and `sharesTraded` and no event-state object; `game_start_time` is
a scheduled start and is refused by source name. `bettor_progress_providers`
states the exact capability required — fixture identity, an **observed**
period, the provider's own timestamp, a status separating in-play from
break/suspended/abandoned/final, and a refresh at least every 60 s —
registers two adapters against the response shapes that actually occur, and
refuses by name until one is configured. Connecting a provider that meets
that list is configuration, not development.

**The pairing-only experiment is not the management policy.** The policy
has two halves; one is running and one is unavailable for want of an
observation, and the command centre says so in both places.

### 10.5 Regression attribution (item 5)

Collected identities, same invocation, same machine:

| | collected | failed | passed | skipped |
|---|---|---|---|---|
| baseline `fa544da` | 11,876 | 449 | 11,241 | 183 |
| HEAD, quiet run | — | **449** | 11,293 | 196 |
| HEAD, collected now | 11,954 | | | |

**The quiet HEAD run included the new tests.** Its 11,941 total reconciles
exactly against the 11,954 collected now, minus the 13 tests written after
that run started (9 in `test_single_writer_ownership.py`, 4 added to
`test_ext_pinnacle_loop.py`). The +78 collection difference is exactly the
six new files. Two ids differ between the two collections only by a
timestamp embedded in a parametrisation, i.e. the same two tests.

So this is **not** an excluded-tests result: 69 of the new tests ran inside
that suite and the failure identities were unchanged — 0 new, 0 fixed. The
earlier 545 reading came from a run sharing the container with a local
Postgres and repeated production polls, and 24 of its 96 extra failures are
allocator and memory-census diagnostics that measure the host.

### 10.6 Deployment churn (item 6)

This release is one deploy carrying all six items. Before it: the touched
modules' tests (137 passing, the only failures being the two pre-existing
`render-ops.yml` size guards), and the three new SQL statements executed
against a real Postgres rather than assumed. After it, the verification
reads the **writer's own code identity** — a digest of the loop module's
source, the build marker and the pid, taken from the process that wrote the
heartbeat — because a deploy id only says what the service was asked to
run. The collector is untouched and the 2026-09-24T04:00:00Z stop is
unmoved.

## 11 · Run 24 — the venue refusal, traced to its root cause

Run 24, job 107478481165, 03:16–03:21Z, against API deploy `e2f9999`
**confirmed by the writer's own identity**, not by the deploy id:

```
external {"pid":1,"build":"e2f9999c43ec...","module":"...ext_pinnacle_loop",
          "source_sha256_12":"7568e6b96021"}
model    {"pid":1,"build":"e2f9999c43ec...","module":"...rn1x_model_loop",
          "source_sha256_12":"4b1400d6a22d"}
writer_ownership  OK   4 of 4 loops hold their own lock, one pid each
```

**Both fixes took effect, and neither was the cause.**

- The line-market refusal fired on live data:
  `VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE 1`. A totals contract
  can no longer be priced off a moneyline probability.
- The recency bound worked: every slug in the census is now a **current**
  fixture — `mlb-ari-col-2026-09-23`, `mlb-laa-oak-2026-09-23`,
  `mlb-hou-sea-2026-09-23`, `mlb-stl-pit-2026-09-24`,
  `mex-caz-tol-2026-09-26`. No more 19-day-old rows.

**And all six still answered `NotFoundError`.** So the cause is neither
staleness nor market type. It is this:

> **I am asking the US venue for identifiers from the global catalogue.**
> `pmus.book_read` takes a **US market slug**. The working readers get
> theirs from **`us_premap.market_slug`** (`workers/bettor_state.PREMAP_SQL`
> selects `identifier, market_slug, event_slug, side_norm, kind,
> sports_type, team_league, game_start`). My loop takes `markets.slug`,
> which is the global catalogue's slug for the same fixture. The venue has
> never heard of it, which is exactly what `NotFoundError` means, and it
> would have said so on the first cycle if the counter had carried the
> slug — which is what run 23's split made possible.

**Why this is a design correction and not a patch.** `us_premap` has no
`condition_id` column; it is keyed by the venue's own `identifier` and
`market_slug`. So driving candidates from it — which is the right answer,
because it is the venue's tradeable catalogue with `team_league` and
`game_start` already on it — changes **which identifier the valuation
persists**. `external_valuations.condition_id`, the census grouping and the
trace read all key off the global condition id today. That is a schema and
contract change across four call sites.

**I am not starting it inside this window.** It is 03:26Z, the observation
stop is 04:00Z and fixed, and the honest options were a rushed half-release
or a precise handover. The work, in order:

1. add the US contract identity to `external_valuations` (a migration:
   `us_market_slug text`, kept alongside `condition_id`, not replacing it);
2. drive candidates from `us_premap` bounded by `game_start`, mapping
   Pinnacle fixtures onto `team_league` + `game_start` rather than title
   tokens — strictly more information than the title match has;
3. refuse `NO_US_VENUE_CONTRACT_IN_PREMAP` **before** spending a venue read,
   so a fixture the venue does not list costs nothing;
4. keep the global `condition_id` on the row when one is known, so the
   existing census and trace keep working.

Nothing about this weakens a refusal or raises a budget: step 3 *reduces*
venue reads, which is the collector's budget being spent on questions with
no answer.

## 12 · Acceptance, against production records only

Read 03:16–03:21Z unless stated. `✓*` is a controlled observation.

| # | requirement | state | production evidence |
|---|---|---|---|
| 1.1 | odds credential usable by the running process | ✓ | 20/20 EPL events with Pinnacle h2h, age 28.8 s, `key_value_returned false` |
| 1.2 | exact contract mapping | ✓ | 4,000 markets considered; 37 `NO_VENUE_CONTRACT_FOR_EVENT`, 1 ambiguous, **1 line market refused** |
| 1.3 | line market never priced off a moneyline | ✓ | `VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE 1`, live |
| 1.4 | candidate set bounded to live markets | ✓ | every census slug a current fixture; no row older than 2 days |
| 1.5 | contemporaneous venue price + depth | **✗ BLOCKED** | 6 × `NotFoundError`. Root cause §11: global slug against a US endpoint |
| 1.6 | venue refusal is diagnosable | ✓ | per-slug stage, endpoint, code, status, sanitized detail, on demand |
| 1.7 | settlement rules attested | **✗** | `ATTESTED` empty; soccer 3 unmet, baseball 2. Vetoes admission, so the first complete record decides NO_TRADE |
| 1.8 | one persisted valuation with probability → cost → edge → decision | **✗ NOT ACHIEVED** | 0 rows. Blocked at 1.5, and 1.7 would make it NO_TRADE |
| 2.1 | prospective lane: process ≠ evidence | ✓ | badge **CHECK**: 4 positions, **0 runtime-decision**, 4 legacy backdated |
| 2.2 | each prospective refusal named | ~ | `REPLAYED, examined 400, written 0, refusals {CLASSIFY/UNKNOWN 2, SEED/INITIAL_ENTRY 1}` — accounts for 3 of 400. The rest are scanned fills that never became candidates; the census does not yet say so per fill |
| 3.1 | predictions mature and join | ✓ | run 23: **159 joined**; run 24: 191 matured, next maturity 03:23:59Z |
| 3.2 | evaluation persisted when eligible | ✓ | run 23 stage 5: `n 159, ok true, base_rate 0.1069, is_out_of_sample true` |
| 3.3 | censoring preserved | ✓ | open horizons unjoined; a closed horizon with no complement fill is a genuine 0 |
| 4.1 | event-progress source identified | ✓ | none exists: scores endpoint `progress_fields []` (three reads), venue payload has no event state, `game_start` refused by source |
| 4.2 | integration prepared | ✓ | `NO_PROGRESS_PROVIDER_CONFIGURED`, 2 adapters, capability stated, 11 tests |
| 4.3 | pairing not presented as the whole policy | ✓ | `second_half_loss_exit UNAVAILABLE` beside `pairing LIVE` |
| 5.1 | single-writer ownership | ✓ | 4 of 4, one pid each, `doubled []` |
| 5.2 | standby writes nothing | ✓* | controlled, real Postgres, through `run()` |
| 6.1 | writer code identity verified | ✓ | both writers report `build e2f9999…` with distinct source digests |
| 6.2 | processing continues after deploy | ✓ | 423 orders, 160 settled, 1,177+ predictions, cycles at 03:21Z |
| 6.3 | collector preserved, stop unmoved | ✓ | no worker deploy; 04:00:00Z stop untouched |
| 7 | funded orders unavailable | ✓ | gate authorizes first at the adapter; AST-verified |
| 8 | no test regressions | ✓ | 449 = 449, identical ids, new tests included |

**Profitability is not claimed.** The only out-of-sample number here is a
base rate on 159 joined predictions of a cohort-behaviour target that is
explicitly not a settlement forecast and not our fill probability.

## 13 · The exit feed: what we lack, what exists, and what to buy

### 13.1 Our integrations lack the field. That is measured on both.

| integration | how asked | result |
|---|---|---|
| odds provider `/scores` | live request, three times (02:08:18Z, 02:33:10Z, 03:21:15Z) | HTTP 200, `progress_fields []`, `carries_observed_period false` |
| venue `events.list` | `venue_event_progress_probe` reports the payload's own KEYS | the event record carries `slug`, `title`, `startTime`/`startDate`, `endTime`/`endDate`, volume and market boards. A `status`/`state` key is a market state, not a period |
| venue `markets.book` | read every cycle | quotes, ladders, `stats.sharesTraded` |
| venue `markets.settlement` | `bettor_live_read.read_settlement` | the settlement PRICE after resolution — post-hoc, not progress |
| `game_start_time` | held already | a SCHEDULED start. Refused by source name, deliberately |

**"Our current integrations lack the field" is established. "No accessible
source exists" is not, and I am not claiming it** — live period and clock
data is an ordinary product category. The distinction matters because the
first is a purchasing decision and the second would be a dead end.

### 13.2 The exact capability to buy

A live in-play state feed. The five requirements are already machine-
readable in `bettor_progress_providers.CAPABILITY`; in procurement terms:

| # | requirement | why it is not optional |
|---|---|---|
| 1 | fixture identity: both team names (or a stable id) **plus** kickoff date-time | it has to map to a venue contract through `bettor_venue_mapping`; the venue dates some fixtures a calendar day either side, so the timestamp is what binds |
| 2 | an **observed** period / half indicator (1 or 2 for soccer) | the halfway rule needs the period, not the score. 0-0 is the same string in minute 5 and minute 80 |
| 3 | the provider's **own timestamp** for when that was true, to the second | our receipt time makes every observation look fresh by construction |
| 4 | a status separating in-play / half-time / suspended / abandoned / final | four different reasons not to sell, and `bettor_progress_feed` refuses all four distinctly |
| 5 | refresh **at least every 60 s** while in play | `MAX_AGE_S` is 120 s; an observation older than that does not license an exit |

Not required: clock to the second, possession, lineups, odds, xG, or
historical archives.

**Coverage required, in priority order:** soccer first — it is the only
sport where this experiment has both a Pinnacle price and a venue contract
today, and the leagues seen in the candidate set are EPL and Liga MX.
Basketball and football halfway rules are already written
(`DOCUMENTED_MAPPINGS`), so a feed covering NBA and NFL connects them with
no code change. Hockey has no written rule and needs one first.

**Cost: I cannot give you a number from here.** This container has no
vendor access and quoting a price from memory is the kind of invented
figure this delivery has been correcting all night. What I can give you is
the exact request to send: *"an in-play state feed for soccer (EPL, Liga
MX), covering every fixture we may trade, returning fixture identity with
kickoff timestamp, an observed period/half indicator, the provider's own
observation timestamp, and a status distinguishing in-play, half-time,
suspended, abandoned and final, refreshing at least every 60 seconds while
in play — plus per-request or per-month limits and price."* Three or four
vendors sell exactly that shape.

### 13.3 The adapter is ready and refuses until it is fed

`bettor_progress_providers` registers two adapters against the two response
shapes real scores APIs use, refuses with `NO_PROGRESS_PROVIDER_CONFIGURED`
until `PROGRESS_PROVIDER` names one and its credential is present, and puts
every observation through `bettor_progress_feed.validate` and the 120 s
staleness bound on the way past. Eleven tests pin the refusals, including
that an unknown status is never rounded to IN_PLAY and that a derived
source cannot be smuggled through with a real period and a real timestamp.

**Connecting a conforming provider is configuration, not development.**

### 13.4 The policy is not pairing-only, and is not complete either

`pairing` reads LIVE and `second_half_loss_exit` reads UNAVAILABLE, side by
side, in the same tile set. The frozen policy has two halves; one operates
and one waits on an observation nobody has bought yet. Neither the
dashboard nor this document describes the operating half as the policy.

## 14 · A BUY is unreachable on this path, and that is by construction

Worth stating plainly before any acceptance table, because it bounds what
item 2 can ever deliver. Two **independent** refusals stand between a
complete valuation and a BUY:

1. **The venue's settlement rules.** After the draw attestation (§12 of the
   acceptance checklist) soccer has two unmet rules left — overtime and
   void — and any unmet rule is handed to the engine as a caller refusal
   which vetoes admission.
2. **`p_fill` is NOT_IDENTIFIED.** `bettor_entry_gate` refuses with
   `EXECUTION_ESTIMATE_NOT_IDENTIFIED` whenever `p_fill` is None or outside
   (0, 1]. The external loop supplies `None` **deliberately** — the depth it
   reads is *displayed* depth, which is an observation and not a queue
   position, and a test (`test_p_fill_is_never_defaulted_into_certainty`)
   exists to stop anyone substituting a number.

So even with every settlement rule attested, this path would still refuse.
**The reachable outcome is a complete record carrying a NO_TRADE decision**,
which is what the directive accepts as evidence that the path operates, and
it is the only outcome that can be honestly produced without inventing a
fill probability.

Verified end to end against a real Postgres before deployment: Pinnacle
1.90/2.00 → de-vigged p = 0.5133 → US contract identity → ask 0.47, depth
900 → fee 0.01 → edge +0.0333 → **NO_TRADE** with both refusals named →
persisted, with the second identical persist correctly skipped as a
duplicate.

A positive estimated edge with a refusal beside it is the honest shape of
this result. Reporting the +0.0333 without the two refusals would be the
overstatement this delivery has spent the night removing.
