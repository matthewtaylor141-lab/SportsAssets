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

All behind the COMMAND session. **Eleven statuses**, each naming what it is
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

The three lanes stay visibly separate by experiment id:
`RN1X_MGMT_PAIR091_STOP16_V1` (historical replay),
`..._PROSPECTIVE` (prospective RN1 management),
`EXT_PINNACLE_DEVIG_V1_SHADOW` (independent external-valued shadow).

## 4 · Observed evidence, with timestamps

**Authenticated production read, run 19, 2026-09-24T01:32–01:37Z:**

```
historical_replay           LIVE          prospective_rn1_management  LIVE
independent_ev_entries      BLOCKED       pairing                     LIVE
second_half_loss_exit       UNAVAILABLE   accounting_health           OK
learning_evaluation         LIVE          external_valuation          ARMED
order_book_state            LIVE    370 modelled orders, both lanes
shadow_pnl                  LIVE    134 settled positions
model_fitting               ARMED   COHORT_COMPLEMENTARY_FILL_WITHIN_H
```

**The odds credential works in the running process** (01:32:20Z): HTTP 200,
`retrieved true`, 20 of 20 EPL events carrying Pinnacle h2h, credits
8,073,099 used / 6,926,901 remaining, `key_value_returned false`.

**The clock audit** (01:34:52Z): **136 positions, 0 able to support a
prospective claim, 130 labelled `BACKDATED_TO_AVAILABILITY_UNAUDITED`,**
6 `REPLAY_AT_AVAILABILITY`. All four pre-existing "prospective" positions
are in the backdated set. They are preserved and reclassified, never
rewritten — their receipt instants were overwritten before migration 104
and cannot be recovered, so inventing one would be the original defect.

**The model loop fitted on production data** (01:36:53Z): 40,000 source
rows → 7,805 built, base rate 0.3523, 2,750 positives, `dataset_sha`
`8c110c1b9bfc70cc`, `model_version` `1.8c110c1b`,
`unmatched_to_source_fill 0`. In-sample log loss **0.5915** against a
**0.6489** baseline — labelled `IS_IN_SAMPLE`, **a fit diagnostic and not
performance**.

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

*The external valuation will rarely clear on the bulk endpoint.* Quote age
measured **32.7 s** (01:06:57Z) and **36.2 s** (01:32:20Z), against the
engine's own **30 s** freshness rule, with LigaMX at 29.8 s earlier. The
limit was **not changed**: raising it would manufacture activity by
loosening a risk parameter. Remedies are the per-event endpoint or an
explicit management decision on the threshold.

**Not established, and not to be read as established:**

- **No opportunity has been found, and none ruled out.** `external_valuation`
  has evaluated 0. With the venue settlement rule unattested, every
  evaluation refuses before admission on that ground alone.
- **No calibration, no net shadow return, no execution sensitivity.** All
  need outcomes joined to predictions recorded beforehand. The ledger had
  0 joined at last read.
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
| 2 | external valuation refuses on freshness | decide whether to try the per-event odds endpoint (more credits, fresher) or to revisit the 30 s rule deliberately. I will not move a risk parameter to create activity |
| 3 | venue settlement rule unattested | capture the venue's per-market rules text, or measure the rule from `resolved_prices` on fixtures decided after 90 minutes |
| 4 | no calibration or net return yet | time. Predictions must be recorded, then their horizons must close |
| 5 | `render-ops.yml` has 422 bytes of headroom against a 512,000-byte ceiling, already below this repository's own 32 KiB rule | it is the lever that operates Render and holds the trading kill switch. It needs splitting. I did not add to it |
| 6 | branch reconciliation | `claude/command-center` is ahead of the default branch. Everything is deployed by commit id, so nothing here depends on merging. Say the word and I will reconcile |
| 7 | pre-existing test debt | 545 failures across 88 files in the full suite; on the eight worst files the pre-session commit and HEAD give **identical** failure sets (116 failed / 465 passed). A full-suite baseline is being measured |
