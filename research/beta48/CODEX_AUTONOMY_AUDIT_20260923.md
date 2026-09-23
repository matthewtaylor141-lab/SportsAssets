# Autonomous system audit — 23 September 2026

## Verdict

The system is not operational as an independent autonomous investment
strategy. This is a source-code and read-only production audit, not a
restatement of prior handoffs. The fixes in this branch are not deployed.
They do not qualify a strategy, enable funded orders, resume the damaged
account, or alter the observation collector.

Source inspected: `e91d493b6fb2c2c5262e8843ae80f8f239d0253d` on
`claude/command-center`. API deployment read directly from Render:
`e289d0b96ad60b16568adfbc23cb1c08f841c22c`, live since 17:06:19Z.
These are different revisions. The audit branch preserves that distinction.

## Production observations

Read-only Postgres query at 18:34:19Z:

- Account `acct_fc2d773a2afa4851` is ACTIVE in the account register but
  `paused=true`, `ACCOUNTING_UNCERTAIN`, `last_verified_at=NULL`.
  ACTIVE is therefore not proof of operating health.
- No `rn1x_*` tables exist in the production public schema.
- The model register contains `rn1_complement_1h` versions 1 and 2.
  Version 1 is `SUPERSEDED_TARGET_MISMATCH`; version 2 is `FROZEN`.
- The prediction table contains five predictions, all
  `INVALID_AS_ENTRY_TIME`; latest recorded time 12:44:16.819211Z.
  This is not evidence of a continuously operating learning loop.

Subsequent one-hour decision census: 165 `BETTOR_EV_SHADOW` decisions,
all `NO_TRADE`, latest 18:37:56Z; 177 `RN1_SHADOW` decisions, all
`NO_TRADE`, latest 18:38:45Z. This describes those lanes and interval only.

## Actual running connections

| Path | Source evidence | Finding |
|---|---|---|
| Independent collection | `workers/all.py` → `workers/shadow_bettor.py` → `shadow_bettor.decide` | Registered worker, records observations and decisions. |
| Independent selection | `shadow_bettor.decide` → `bettor_ev_bridge.evaluate` → `shadow_lanes.not_yet_eligible` | No BUY/SELL branch. Three blockers added unconditionally; increasing data cannot automatically open this gate. |
| Independent valuation | `bettor_ev_bridge.fair_value` | Returns venue midpoint benchmark; independent fair value remains NOT_IDENTIFIED. No registered-model scorer is called here. |
| Alternate decision-only worker | `workers/bettor_prospective.py` | Explicitly not registered in `workers/all.py`; not the independent production lane above. |
| Complement adapter | `bettor_observation_adapter.to_book` | Sets the NO book to None. This is another path, not an explanation of every refusal in the primary lane. |
| Experimental desk | API lifespan → `bettor_desk_loop.run` → `DK.Desk.step` | Env-enabled process, account pause applies. Constructs `DK.Policy()` without a learned curve. |
| New RN1 experiment | `bettor_rn1x_run.run` / management lifecycle | Batch runner; no API/worker caller, no database persistence caller, no published trace route. Migration existing in git is not production deployment. |
| Learning | training scripts and `tools/desk_experiments.py`; model/prediction register | Offline tools exist. No continuous production training/evaluation/promotion call path found in inspected API/worker/workflow sources. |

## Verified defects repaired in this branch

1. **Broken runner dependencies.** The runner imported a nonexistent
   `FEES` object and referenced removed policy constants/source labels.
   It now loads the schedule module and calls management's current
   `.91 / second-half .84` policy through the existing lifecycle.
2. **Same-event execution bias.** A print generated a decision and then
   filled its new order. Fills now precede decisions, require an order
   strictly older than the source print, and exclude expired orders.
   Decisions are walked by receipt time, not source time. Tape prices
   are no longer presented as executable bid/depth observations.
3. **False prospective classification.** An unresolved historical batch
   was labelled FORWARD_SHADOW. The batch runner now always says
   HISTORICAL_REPLAY, `prospective=false`.
4. **Unsupported initial inventory.** A truncated first BUY was enough
   to assign initial inventory. The runner now refuses classification
   unless the caller explicitly supplies verified initial-inventory
   status. That assertion still needs external supporting evidence;
   the flag itself is not proof.
5. **Concurrent management orders despite a one-order declaration.**
   `place` requested cancellation and immediately placed its replacement.
   It now returns WAIT_CANCEL_ACK, allows intervening fills, and requires
   reassessment after acknowledgement. Sales cap at residual inventory,
   preserving already matched pairs.
6. **Unnecessary cancel/repost.** Unchanged management intent now retains
   its existing order. Pair prices and loss triggers use the same supplied
   fee function as simulated execution. Rebate assumptions cannot make
   the pairing price more aggressive.
7. **Writer handover never retried.** A standby desk loop slept forever
   without attempting its lock again. It now retries before any startup
   work. The API retains the desk task and cancels/awaits it before pool
   shutdown, so that task does not keep the connection indefinitely.
8. **Rollback left the Python book advanced.** After a cycle error the
   loop now restores a fresh portfolio and cursor from durable state
   before processing again, including uncertain commit acknowledgement.
   Failed first persistence retains the original startup cursor instead
   of skipping to a newer feed head.

Tests previously counted only RESTING orders and ignored CANCEL_PENDING;
that assertion passed while two orders could execute. It now checks the
whole open-order set. Other existing tests expected the old ability to
sell matched inventory; those expectations were corrected explicitly.

## Verification

176 focused tests passed in the isolated development environment:

```
python -m pytest -q \
  tests/test_autonomy_integration_audit.py \
  tests/test_bettor_mgmt_lifecycle.py tests/test_bettor_mgmt_router.py \
  tests/test_bettor_entry_kind.py tests/test_desk_recovery.py \
  tests/test_desk_accounts.py tests/test_bettor_desk.py \
  tests/test_desk_experiments.py
```

Tests cover policy-to-order integration, no same/earlier-print fills,
late receipt, repeat events, cancellation races, retained matched
inventory, controlled event-phase gating, lock handover, and recovery
after transaction failure. DB recovery tests use the repository's fake
transaction store; they are not a real-Postgres or production restart
acceptance. No funded order or venue collection request was made.

A controlled 100 YES @ .60 scenario pairs on a subsequent .30 NO print,
with a labelled test fee, and reconciles to +9.70 at settlement. This is
a software test, not observed profit or an executable return claim.

The initial test invocation failed during fixture setup because the new
local environment lacked asyncpg. Dependencies were installed into an
isolated venv. No production dependency declaration was changed. A broad
suite was not run; older global failure counts are not attributed here.

## What still prevents full operation

### RN1 management

- No production RN1 experiment tables/worker or live UI trace yet.
- No admitted event-progress mapping in `SECOND_HALF_MAPPING`. The loss
  exit is unavailable; pairing-only simulation must not be described as
  the complete management policy. A documented observed sport-phase
  source, point-in-time join and freshness validation are required.
- Quote/depth and contract identity must be attached from the correct
  venue. Cohort prints are not a standing book or our fills.
- Default replay fees remain a labelled transferred PMUS-latest scenario,
  not historical same-venue fees. Use a verified venue/date fee provider
  before economic comparison. Tick and fill-piece fee rounding also need
  venue-specific treatment.
- `Managed` is not a persistent live account: its local IDs/checkpoint
  recovery are not yet wired to `rn1x_*`. Do not deploy it as a continuous
  writer on the strength of these pure lifecycle tests.
- RN1 subsequent-action count is not an attributed management-return
  benchmark. Quantity attribution and full lifecycle reconciliation are
  still required.
- Current script does not prove capital sufficiency or capacity. Assigned
  inventory and negative synthetic cash are experimental bookkeeping,
  not a funded portfolio.

### Independent entries

The primary lane intentionally returns refusals regardless of action
table values. Replacing that needs a versioned model/feature scorer,
execution estimates for the specific action, sizing/risk, and the same
verified persistent order path. An RN1 complement forecast is not a
settlement forecast or our fill model. Do not simply remove the refusal
or relabel midpoint as independent value to make a dashboard active.

### Continuous improvement

The missing operating connections are: prospective input/prediction
logging, mature-label/completeness joining, scheduled candidate training,
artifact registration, evaluation on new data, promotion/rejection
receipts, and a runtime model loader with rollback. Use the existing
kernel, scripts and registry; do not build a competing research stack.

Promotion must compare against a frozen champion after costs with
execution and accounting gates. Retain the champion when a challenger
does not improve. More data cannot guarantee that a strategy improves
monotonically, and model promotion is not permission for the agents to
rewrite risk limits, management policy or order authority.

## Release boundary

This branch targets `claude/command-center`, not the shared auto-deploy
branch. Do not deploy the whole branch solely to obtain the handover fix:
it also carries upstream work not accepted here. No production schema,
configuration, account, control, schedule or service was changed by this
audit. The full autonomous-profitability request remains incomplete.

Publication attempted: shell Git has no authenticated push credential.
The connected GitHub API rejected creation of the isolated audit branch
with HTTP 403, `Resource not accessible by integration`. This is a
connector permission failure, not a requested additional deployment
approval. An apply-ready git patch is provided instead; it is based on
the pinned source above and must be reconciled with any later Claude edits.
