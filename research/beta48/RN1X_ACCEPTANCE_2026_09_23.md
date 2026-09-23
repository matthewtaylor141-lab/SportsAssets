# Acceptance table — 23 September 2026

Shadow-only authorization. No funded orders. The paused
accounting-uncertain account, the observation collector and all existing
limits are preserved.

Every "observed evidence" cell is read from production Postgres, the Render
deploy record, or a named test run. Where a cell says a thing is not done,
that is the cell's point.

---

## 1 · Historical vs prospective

| Requested | Deployed | Observed | Blocker |
|---|---|---|---|
| Establish whether the experiment is historical or prospective | Two lanes, separately keyed: `HISTORICAL_EXPERIMENT_ID` / `PROSPECTIVE_EXPERIMENT_ID`, `rn1x_source_cursor` / `rn1x_prospective_cursor` | Both declared in `workers/rn1x_shadow.py`; 32 real-Postgres tests incl. different ids, different cursors | — |
| Trace source query, ingestion lane, cursor ordering, timestamp semantics | `_CANDIDATES_HISTORICAL` requires `m.resolved`; `_CANDIDATES_PROSPECTIVE` requires `NOT m.resolved` + live lanes; both `ORDER BY t.id` (serial, monotonic — a timestamp can repeat) | **The experiment was historical because of MY filter, not the feed.** Cursor defaults to 0 → walking from `trades.id` 1 of ~6.1M; observed at 294 | — |
| Separate backfill from newly received activity | `trades.source`: `chain`/`poll` live, `backfill` historical, `s1` synthetic. `LIVE_SOURCES` / `SEEDABLE_SOURCES`; `lane_census` measures latency on live lanes only | Migration 033 states the rule; **my query had no source filter at all, so `s1` emitter rows were eligible seeds** — now excluded and tested. Census at 20:39:41Z over 24 h: **live 49,586 · backfill 0 · s1 427** | — |
| Investigate chain and other live paths before declaring a feed blocker | Traced `ingestion/pipeline.py` (`detected_at = now()` at insert) and `ingestion/history.py` (`source='backfill'`) | **`FEED_POSTDATES_SETTLEMENT` WITHDRAWN,** and the measurement is what withdrew it: live-lane detection latency **avg 72.9 s, max 4,129.2 s (68.8 min)**, with **9,444 live unresolved cohort BUYs in 24 h**. The 15.07 h figure was one seed, and **all 65 historical positions came from one lane: `seeded_by_source = {"backfill": 65}`** — on a backfill row `detected_at − ts` is the age of the backfill, not a detection latency | — |
| Do not reset the historical cursor or discard its evidence | Historical cursor still defaults to 0 and is never written backwards; only the prospective cursor seeds at the feed head | Test: a prospective cycle does not move the historical cursor | — |
| Persist historical and prospective separately | Different experiment ids; prospective `_replay_one` **refuses to read a payout even if the market resolves mid-cycle** | Test: prospective run has `scored=False`, `resolved_at=None`, `SETTLE.settled=None`, while the historical run on the same data scores. In production at 20:39:41Z the two lanes sat at **different cursors on the same table — prospective 221,545,303 (the feed head), historical 294** | Prospective lane has produced **no positions yet** (`IDLE_NO_CANDIDATES`) — its cursor starts at the feed head, so cohort BUYs on unresolved markets must arrive after it started. **Not a feed blocker: the lane is ARMED and the feed is delivering 9,444 eligible BUYs a day** |

## 2 · The complete management policy

| Requested | Deployed | Observed | Blocker |
|---|---|---|---|
| Trace available event-progress integrations | Only `game_start_time` from the CLOB market record, consumed by `edge_marks.fetch_game_start`, `underdog`, `premap`, `clob` | **No period, half, quarter, inning, clock or score signal exists** in the schema or the code | — |
| Connect a reliable second-half signal for supported sports | `DOCUMENTED_MAPPINGS` (soccer 2, basketball 4, football 4; hockey `None` — odd structure, no rule); `PROGRESS_FEED_CONNECTED` empty; `SECOND_HALF_MAPPING` = the intersection | Three absences named separately: `NO_HALFWAY_RULE_FOR_THIS_SPORT`, `PROGRESS_FEED_NOT_CONNECTED`, `PROGRESS_OBSERVATION_MISSING_NOW` | **NOT CONNECTED.** Missing: a timestamped per-sport progress observation (period index + in-play), joined point-in-time |
| Do not infer the second half from wall-clock | The progress contract is `(observed_at, period, period_type, in_play)` — **no elapsed field exists**, so a rule physically cannot do it | `PROGRESS_FIELDS`; rules consume only `period` | — |
| Implement and demonstrate the 16% loss trigger | `pol.loss_trigger` + `Managed.manage_policy`, driven through the real lifecycle | 14 controlled tests. Fires in 2nd half, never in 1st; stoppage and unadmitted sport refuse | Needs a progress feed **and** an executable bid to fire on real evidence |
| …cancellation acknowledgement | `place()` returns `WAIT_CANCEL_ACK`; `acknowledge_cancels` frees the slot | Test: exactly one order live, in `CANCEL_PENDING`; replacement refused until ack | — |
| …late fills | `on_print` fills a `CANCEL_PENDING` order and flags `raced_a_pending_cancel` | Test: fill lands before ack, book reconciles | — |
| …remaining inventory | Depth caps the sell; residual stays under management, carried at **cost**, never marked | Test: 40 of 100 sellable, 20 filled, 80 held, `mark_usd = NOT_IDENTIFIED` | — |
| …accounting | `invariant.ok` asserted on every path | **Measured: 0.57 basis, 100 contracts, executed 0.47 → realised −11.73 on 57.00 = a 20.58% loss against a 16% TRIGGER** | — |
| Explicitly exclude unsupported events | `admitted_to_complete_policy` is False unless a rule **and** a feed exist | Test: `cricket` and `hockey` both excluded, each with its own reason | — |
| *(found while doing this)* | `trigger_level_bid` was 84% of cost computed **without** the exit fee, while the rule correctly fires on net proceeds | **Reported 0.4788 when the exit truly fires at 0.49** — understated by 1.74 ¢. Both levels now reported; boundary asserted exact | — |

## 3 · The independent entry path

| Requested | Deployed | Observed | Blocker |
|---|---|---|---|
| Show the actual caller chain | `workers/all.py` → `workers/shadow_bettor.py` `tick` → `shadow_bettor.decide` → `bettor_ev_bridge.evaluate` → `shadow_lanes.not_yet_eligible` | **Confirmed unconditional NO_TRADE.** `decide`'s own docstring: "has no path that produces a BUY or a SELL" | — |
| Identify the commit that replaces it, or finish | **No such commit exists.** Closest is `869b662` "Wire the built EV machinery into the BETTOR decision lane" — wired the evaluation, left the refusal unconditional. So: finished it | `bettor_entry_gate.admit`, six requirements, wired into `decide` | — |
| Demonstrate an admissible BUY **and** a correct refusal through the same path | Both through `shadow_bettor.decide`, no monkeypatching | 23 tests. A test asserts the two cases differ **only in their inputs** | Inputs are **supplied by the test** — labelled controlled tests |
| Do not weaken production requirements | Every requirement parameterised so removing a check fails exactly one test; circularity guard refuses any venue-derived fair value | Production path still refuses on **all six**: `NO_QUALIFIED_MODEL`, `INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED`, `EXECUTION_ESTIMATE_NOT_IDENTIFIED`, `SIZING_POLICY_NOT_APPLICABLE`, `RISK_GATE_BLOCKED`, `NO_ACTION_HAS_POSITIVE_NET_EDGE` | — |
| Does any current model qualify? | `qualify_model` checks target, status and prediction validity independently | **NO.** `rn1_complement_1h` v1 (`SUPERSEDED_TARGET_MISMATCH`) and v2 (`FROZEN`) both fail on **target** — a complement forecast is not a settlement forecast, and retraining does not fix a target. Predictions were all `INVALID_AS_ENTRY_TIME` | **A connected engine without a qualified model.** Needs a settlement-target model with entry-time-valid predictions |

## 4 · The learning system, precisely

| Requested | Deployed | Observed | Blocker |
|---|---|---|---|
| What it actually **fits** | **Nothing.** No training step, no gradient, no likelihood, no calibration, no search | `DESCRIPTION["fits"] == []`, asserted | — |
| What it merely **compares** | Frozen champion vs 4 declared challengers, same assigned inventory, same evidence, whole queue_share grid, via `learn_gate.gate_v2` | `DESCRIPTION["compares"]`, 3 declared comparisons | Cannot discover anything outside the declared list |
| What it **changes** | **Nothing.** Two verdicts exist; no code path edits the policy | `DESCRIPTION["changes"] == []`; a test asserts no UPDATE against the register or policy constants | — |
| Which data each evaluation uses | Settled `rn1x_outcomes`; fills **re-read** from `trades` each cycle; identified by `dataset_sha` + position count; prospective excluded | `DESCRIPTION["data_per_evaluation"]` | — |
| Keep the frozen policy unchanged | `PAIR_TARGET_COST` 0.91 / `LOSS_TRIGGER_FRACTION` 0.84 untouched; challengers vary by **argument**, never by edit | Frozen path still prices the completion at **0.32** on a 0.57 basis | — |
| PAIR_092 is exploratory, not a promotion | Status `CHALLENGER_ELIGIBLE_PENDING_MANAGEMENT`; no promotion path | Eligible at 420 and 598 decided — **and at 649 decided it was NOT.** Heartbeat 20:47:19Z, version 14: `RETAIN_CHAMPION`, "improves cost-marked P&L in **1 of 3** scenarios… has found a liquidity assumption, not an edge". The seven positions the unwedged cursor added were enough to withdraw it. **PAIR_090 is now the eligible one** (version 13), on its first appearance | Nested datasets, so none of these three is a replication of another — and the one that changed its mind shows why that distinction was worth keeping |
| Check the next due cycle by **receipt and heartbeat** | Receipts carry `written` and the reason | **`rn1x_learn` heartbeat 20:38:46Z: `"new_rows": 0`, `"positions": 65`, `"dataset_sha": "8488d060ec7d"`, and all four receipts `"written": false` with "unchanged from version N on the same dataset"** — deduplication confirmed from each receipt's own reason, not inferred from a row count that happened not to move. **And the complement, which is the stronger half:** at 20:47:19Z, after the unwedged cursor moved the dataset to **71 positions / 649 decided**, the same loop wrote `"new_rows": 4`, versions 13–16. It skips on an unchanged dataset and writes on a changed one — so the earlier zero was identity, not inactivity. At 20:51:34Z it then declined again, naming the arithmetic: `TOO_FEW_NEW_POSITIONS`, "72 settled positions against 71 at the last recorded evaluation… a verdict resting on hundreds of decided orders does not change on 1 more" | — |
| *(found while doing this)* | The heartbeat also published `champion_retained`, which production returned as **`false`** | It was only `all(verdict == RETAIN)`, and PAIR_092 is ELIGIBLE — so a correct evaluation printed what reads as *the champion was replaced*. It cannot be: there is no promotion path. Split into `champion_unchanged` (a constant), `champion`, `all_verdicts_retain` and `eligible_pending_management`; a test asserts the old key is **absent** | — |

## 5 · The command centre

| Requested | Deployed | Observed | Blocker |
|---|---|---|---|
| Separate statuses for all seven concerns | `STATUS_KEYS` = historical replay, prospective RN1 management, independent EV entries, pairing, second-half loss exit, accounting health, learning evaluation | `/api/command/rn1x/statuses`; 38 command-centre tests | — |
| A LIVE badge must identify what is live | Three states — **LIVE** (running and producing), **ARMED** (running, nothing produced yet), **STOPPED** — and every badge carries `what` and `why` | Test asserts all three and that each names its subject | — |
| Click path to one traceable position | Row click → six stages: source event, three clocks with **receipt** time, decisions, orders (side/intent/liquidity/state), modelled fills with the print that licensed each, inventory and outcome | `data-rn1x-pos` handler + `/trace/{position_id}`; rendered stages asserted | — |

---

## Production defect found and fixed during this work

The lane change exposed a wedge, observed at 20:34:29Z:

```
{"state":"REPLAYED","cursor":294,"examined":400,"written":0,
 "results":[{"error":"ValueError: settlement must have an observed time
  after the seed","trade_id":295}],"stopped_at_error":295}
```

Trade 295 raised, and because the cursor correctly refuses to advance past
a failed write, that one condition blocked every later one — 400 candidates
examined per cycle, nothing written, indefinitely. A `backfill` row's
`detected_at` can postdate the market's resolution, so this is a real data
shape and needs a refusal, not an exception.

Refused at **SEED**, before any inventory is assigned:
`SETTLED_BEFORE_DETECTION` with the gap in hours. My first attempt refused
at step 8, which left `MANAGE` already run and would have written a
position with no orders — the half-written shape that made the desk's book
uncertain.

**Cleared in production, measured at 20:49:23Z.** The cursor moved
**294 → 334** (and **350** by 20:55:25Z — it is still walking), positions
**65 → 72**, outcomes 72, decisions 2,748, orders 216, fills 23. Trade 331
wrote a whole position — 6 decisions, 1 order, `outcome: true`,
`reconciles: true`. Trades 333 and 334 refused at `CLASSIFY` with
`"written": false` and the refusal returned to the caller as a blocker,
which is the half-written shape not happening.

The deploy carrying the fix: **`aea4203`, live 20:46:37Z** (`ee5387d`
deactivated), on `sportsassets-api` only — `sportsassets-workers` held
`dep-dapqirrbc2fs73bms6fg live 7f76fd9` across every deploy in this
session, so the observation collector was never restarted.

## A second defect found and fixed

`render-ops`'s `deploy-api-commit` **reports `conclusion: success` when
Render refuses the deploy.** Observed directly: run 35917975665 at
20:44:39Z posted a commit id Render did not have, logged `HTTP 404`,
created no deploy — and the job exited 0 and went green. Nothing shipped,
and the workflow said it had. (Confirmed harmless in this instance: the
worker's deploy list is byte-identical before and after, still
`dep-dapqirrbc2fs73bms6fg live 7f76fd9`.)

Fixed in one line — a non-2xx `$CODE` now `exit 1`s:

```
case "$CODE" in 2??) : ;; *) echo "FAILED: Render refused it (HTTP $CODE); NO deploy was created"; exit 1 ;; esac
```

Verified three ways before committing: the guard exits 0 on 201 and 1 on
404 and 500; the YAML still parses; and the file is **511,578 bytes, 422
under** GitHub's 512,000-byte ceiling (over it, the workflow fails at
startup with no log at all).

I first wrote here that I was **leaving this unfixed** because
`test_e30_post_only_body.py` pins this workflow's sha256 and that pin
passed. That was wrong, and checking it is what showed why: the pin's value
(`0ad40506f7460915`) is stale against the actual file (`0cd389dc6bd13397`)
because `c205528` legitimately widened the readback earlier today, and both
pin tests (`e27`, `e30`) already fail one assertion **earlier**, on
migration `100_rn1_seeded_experiment.sql` being newer than their pinned
`064_`. So this edit changes nothing about their status, and the reason I
had given for not doing the work did not exist.

The caveat still holds for every `deploy-api-commit` run **before** this
commit: a green one is not evidence of a deploy — the `HTTP` line and the
deploy id are. That is how `dep-daq3kp3tqb8s73e7cpog` was confirmed.

## What none of this establishes

- **No realised return.** Every fill is modelled, licensed by an observed
  print by someone else at a declared queue share. `P_FILL` is
  `NOT_IDENTIFIED`.
- **No profitability claim** for the management policy.
- **The loss exit has never fired on real evidence.** Its mechanism is
  demonstrated in controlled tests only.
- **No independent entry has ever been admitted in production.** The path
  exists and refuses.
- **Containment unchanged**: `acct_fc2d773a2afa4851` remains `paused = t`,
  `ACCOUNTING_UNCERTAIN`; last desk decision 16:37:30Z.
