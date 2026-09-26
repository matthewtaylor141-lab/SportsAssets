# Bettor operating desk — delivery record, 2026-09-26

## The URL

**https://sportsassets-api.onrender.com/api/command/bettor/desk/page**
(`/command/desk` redirects there with 307.)

Sign in with the Command desk password on the page itself. Verified in
Chromium against the API origin: the sign-in form renders for an
unauthenticated viewer, the password sign-in opens the desk, a refresh keeps
the session, and the page carries no credential in its HTML, its URL or
browser storage (the cookie is HttpOnly and unreadable from JavaScript).

Verified again from the authorised runner against production, with the
COMMAND cookie **alone** and no operator token:

| read | result |
|---|---|
| mint the session (`POST /api/command/session/operator`) | 200, cookie `bt_command`, `path=/api/command`, HttpOnly, Secure, token **not** in the body |
| `/api/command/bettor/desk/page` | 200 |
| `/api/command/bettor/desk` | 200 |
| `/api/command/bettor/control` | 200 |
| the page again (refresh) | 200 |
| a **write** with the cookie alone | **403** — the read roles stay read-only |
| the page with no credential at all | 401 (sign-in form) |
| `X-Admin-Token:`, `localStorage.`, `sessionStorage.`, `token=`, `password=` in the served page | all absent |

## Identities

| what | identity |
|---|---|
| backend (API), deployed and serving | `fae8fff607644953927b0a4f9316822b43bb82df` |
| the desk page | served by that backend, 50,296 bytes, inlined from `frontend/public/command/desk.{html,css,js}` |
| frontend deployment id | **none — there is no separate frontend deployment.** This repository holds no Netlify credential (no `NETLIFY_AUTH_TOKEN`, no site id among its secrets), so the desk is served from the API origin instead. That is the API-hosted desk you accepted for today. |
| the protected worker | untouched, still pinned at `f5d1c05` |
| the enforced risk rails | digest `a19eb60a28177fc3536487a3e56c7995ab706241a52f32860c1c4b68fa736fb7` — unchanged by this release |

### The gate on this release, compared like for like

Both runs from a **frozen `git archive` checkout**, each with its own freshly
migrated database, `-p no:randomly`, on the same machine within minutes of
each other:

| run | commit | duration | failed | the three P&L pins | the race arithmetic pin |
|---|---|---|---|---|---|
| `GATE_BASE8` | baseline `6d75275` | 447.61 s | **178** | present | present |
| `GATE_REL5` | this release `60de482` | 460.91 s | **178** | present | present |

**The two failure-identity sets are byte-identical** — `comm` reports nothing
new and nothing gone, and nothing newly passing. So this release introduces
**no failure identity**. The earlier 177-versus-174 reading was a
run-condition artefact, and both of its causes are now identified below.

It is still not a clean gate: 178 pre-existing failures remain, and two of
them are explained here rather than fixed.

## The completed demonstration, in production

One position, the whole deployed lifecycle, **chosen inputs**:

- entry through `bettor_entry_inventory.plan_entry` / `persist_entry`
- recurring management through `bettor_rn1x_run.manage_open_position` +
  `bettor_rn1x_store.persist_run`
- a marketable reduction, then a completing exit
- the deployed settlement consumer `bettor_entry_settlement.settle_open_positions`
- accounting recomputed from the ledger rows, not copied from the engine

Measured, in production:

| figure | value |
|---|---|
| bought | 100 contracts @ 62.0¢ |
| sold | 100 contracts @ 71.0¢ |
| held now | 0 |
| fees | $3.20 (the deployed fee hook at $0.016/contract, both sides) |
| realised, net of fees | **+$5.80** |
| quantity identity | bought − sold = held, holds |
| cycles to flat | 7 (first production run; a later call replays and writes nothing) |
| residual settlement | `NOT_APPLICABLE__THE_POSITION_WAS_CLOSED_IN_THE_MARKET` |
| settlement rows written | 0 — no venue settlement response was supplied or invented |
| excluded from strategy performance | true |

Its inputs are **chosen, not observed**, and enumerated on the desk. It
writes no row into `trades`, `markets` or `external_valuations`, so a chosen
print can never be read back as a venue's or a provider's own statement.

**Exclusion verified at the consuming queries, not by a screen label:**

- `command_rn1x.entry_evidence` is scoped to `EXT_PINNACLE_DEVIG_V1_SHADOW`
  and returned 0 inventory rows, none from another book — production says
  "the demonstration does NOT appear in the entry lane read".
- calibration reads `external_valuations`; the demonstration writes none, in
  any experiment, so it cannot enter a calibration sample at all.
- funded readiness lists it nowhere.
- a test asserts it manages **no unrelated inventory**: a real strategy
  position seeded beside it comes out with the same decisions, orders, fills
  and seed, and the strategy input tables are unchanged.

## Controls, with their effects read back from the server

Every action reports **requested**, **applied** (read back from the row) and
**failed** separately. From production:

| action | result |
|---|---|
| pause | ok true, `armed_confirmed false` |
| resume (research scope) | ok true, `armed_confirmed true`, `scope RESEARCH_SHADOW_ONLY` |
| resume with `scope: funded` | **409 `FUNDED_RESUME_IS_NOT_AVAILABLE_FROM_THE_DESK`** |
| account, by canonical id, paused on its row | **refused `ACCOUNT_IS_PAUSED`**, nothing stored — read off the registry, not off a display name |
| activate | **409 `FUNDED_ACTIVATION_PREREQUISITES_NOT_MET`**, with the unmet list |
| any control with no credential | 401 `OPERATOR_SESSION_REQUIRED`; with a read cookie only, 403 `CONTROL_REQUIRES_AN_OPERATOR_SESSION` |
| control state, as the rows read | research lane armed true, funded executor paused true, working modelled orders 3 |

Activation is decided **on the server**, computed from the stored control
rows — the button now sends, because a disabled button establishes nothing
when anybody can POST.

## The operator session, and the activation path completed

### No service credential in the browser

The controls take a **CONTROL-scoped operator session**. The operator
password is sent once to `POST /api/command/session/control`; the server
returns a short-lived HttpOnly `bt_control` cookie on the `/api/command`
path and **never puts the token in the body**. The scope is inside the
signed material, so a read (desk) token cannot satisfy a control challenge
and the control token is refused wherever `require_admin` is the gate — it
opens no `/api/admin` route.

Verified in Chromium against the local build of this release, no admin
token anywhere:

| step | result |
|---|---|
| the page with no credential | sign-in form |
| read sign-in with the desk password | desk opens, cookie `bt_command` only |
| **a write with only the read session** | **REFUSED `CONTROL_REQUIRES_AN_OPERATOR_SESSION`** |
| a wrong operator password | **REFUSED `OPERATOR_PASSWORD_NOT_ACCEPTED`** (401, no cookie set) |
| the right operator password | control session starts, cookie `bt_control` added |
| the cookie read from JavaScript | empty — HttpOnly |
| the password field afterwards | cleared; nothing stored |
| pause, then resume, with the operator session | both **APPLIED**, state read back PAUSED then ARMED |
| the paused account by canonical id, renamed "Perfectly Fine Desk" | **REFUSED `ACCOUNT_IS_PAUSED`** — off its row, not its name |

A wrong password was previously answered with a 200 carrying `ok: false`
(the pattern the read sign-ins use). That is now **401**: a control
credential must not be obtainable by misreading a status line.

### Account and limits are executable, not proposals

- the account is resolved against the **canonical registry**
  (`bettor_desk_accounts`) by `account_id`. A display name is recorded for
  the audit and decides nothing. Refusals, each off the row and each
  demonstrated: `ACCOUNT_ID_NOT_SUPPLIED`,
  `ACCOUNT_ID_NOT_IN_THE_CANONICAL_REGISTRY`, `ACCOUNT_IS_NOT_ACTIVE`,
  `ACCOUNT_IS_PAUSED`, `ACCOUNT_ACCOUNTING_IS_NOT_RESOLVED`,
  `VENUE_CLASS_NOT_ESTABLISHED`.
- the owner's approved limits are **consumed**:
  `bettor_entry_execution.effective_limits` takes the element-wise minimum
  of the frozen rail and the approved value, so an approval can only ever
  **tighten**. A looser number is reported `ignored_because_looser`. The
  frozen digest `a19eb60a2817…` is **unchanged**; the effective digest with
  the owner's four numbers applied is `8e667b126912…`.
- approving a recorded set is **admin-gated and separate**: recording is the
  operator's act from the desk, approving is the owner's and takes the
  service credential the browser never holds. `confirm` must equal the
  recorded per-order limit, so an approval names the set it approves.

### Readiness is derived from evidence; UNKNOWN blocks

| check | where it is derived from |
|---|---|
| funded submission disabled | the code constant |
| account selected and clean | the registry row |
| limits recorded and complete | the stored set |
| limits approved by the owner | the approval flag |
| approved limits tighten the enforced rails | recomputed against the frozen rails |
| venue book freshness basis | the cycle ledger's own `venue_clock.basis` |
| settlement compatibility | each row's `settlement_comparison` verdict |
| market scope metadata | each row's `period` |
| an autonomous entry was admitted unwaived | `admissible` minus research-waived rows, demonstration and acceptance books excluded by experiment **and** provenance |

No evidence is **UNKNOWN**, and UNKNOWN blocks. Measured on a disposable
local database: with the market evidence removed the three market checks go
to UNKNOWN; with real-shaped evidence present they turn true. They are not
constants and they are not unconditional passes.

### Both directions demonstrated

| path | result |
|---|---|
| **positive, TEST venue** — clean canonical account, owner-approved limits, all 9 checks met | **HTTP 200, `AUTHORIZED_FOR_A_TEST_VENUE`**, `funded_submission DISABLED`, `authorises_capital false`. Clicked in Chromium: "activate — APPLIED — AUTHORIZED_FOR_A_TEST_VENUE", nine PASS pills, funded executor still PAUSED, orders submitted 0 |
| the same account bound to `PMUS` (FUNDED) | 409 `FUNDED_ACTIVATION_REQUIRES_THE_OWNERS_WRITTEN_AUTHORIZATION` — every check met and it still refuses |
| limits recorded but not approved | 409 `LIMIT_SET_NOT_APPROVED_BY_THE_OWNER` |
| any check unmet or UNKNOWN | 409 `FUNDED_ACTIVATION_PREREQUISITES_NOT_MET`, with the list |
| the paused account | 409 `ACCOUNT_IS_PAUSED` |
| an unknown venue | 409 `VENUE_CLASS_NOT_ESTABLISHED` |
| a demonstration entry beside it | does **not** turn the admission check true — its inputs are chosen |
| a research-waived admission | does **not** qualify — excluded by the row's own risk verdict |

Two defects the live runs found and this release fixes: the route raised
**409 unconditionally**, so a complete authorisation was indistinguishable
from a refusal to any caller reading the status; and the authorisation was
written **over the account binding it had just read**, so the desk showed
"no account bound" immediately after authorising one. The authorisation now
has its own key, and the desk shows the binding and the authorisation as two
separate facts.

**What it authorises:** the operating path against a venue **sandbox**,
under the effective limits. Not capital, not a funded venue, and not a
raise to any frozen rail.

## Capacity, and the isolation defect that had to be closed first

**The defect (mine):** the harness's writer phases stamped the autonomous
strategy's experiment id on every synthetic plan. Enumerated, not assumed:
**2,249 probe positions across four databases, 2,080 of them in a strategy
book**, with their orders, fills and decisions.

**And the earlier explanation of why that was contained was wrong.** I
reported zero `external_valuations` rows as the evidence of no exposure or
P&L impact. It is not evidence of that. **Positions and fills feed exposure
and per-lane P&L directly** — no consumer of either reads
`external_valuations` on the way to a position — so a probe position with
its orders and fills would have entered both with no valuation row in
sight. `external_valuations` is the *calibration* sample's input, and its
being empty says only that the calibration sample saw nothing.

**The evidence that actually establishes it** is the positions and what
hangs off them: **zero identified probe positions in production, and zero
of their dependent records — 0 orders, 0 fills, 0 decisions, 0 outcome
rows** — read from production itself, with the probe identified by the
fingerprints the harness writes and never by an experiment id. The
enumeration keys on positions and walks down to their dependents, in that
order, for the same reason.

- **Environment:** all four (`cap1`, `cap2`, `cap3`, `cap4`) are disposable
  local databases inside this session's container at `127.0.0.1:5432`. The
  probe takes an explicit `--dsn` and was never given anything else; no
  workflow invokes it, so it never ran on a runner holding the production
  credential.
- **Production, asked of production itself** (read-only, via
  `GET /api/admin/capacity-probe-audit`): **0 probe positions, 0 in a
  strategy book, 0 orders, 0 fills, 0 decisions, 0 outcome rows** — and, as
  one further consumer that saw nothing rather than as the proof, 0
  valuations — against 508 positions total. `clean: true`,
  `unreadable: no`. Books present: `RN1X_MGMT_PAIR091_STOP16_V1` 499,
  `RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1` 4,
  `RN1X_MGMT_PAIR091_STOP16_V1_PROSPECTIVE` 4,
  `CONTROLLED_DEMONSTRATION_EXTERNAL_VALUATION_V1` 1.
- **Manifest:** every affected id is preserved in
  `research/evidence/CAPACITY_PROBE_AFFECTED_IDS.json`.
- **Correction:** the four databases held *nothing but* probe records, so
  they were **rebuilt** from the migrations. Nothing was deleted by
  experiment id and no genuine row was touched. The re-audit finds **zero**
  probe records in every local database.
- **Boundary, enforced before the first write**, each with its own refusal
  name and each verified to write nothing: a strategy experiment is refused
  (`REFUSED_A_STRATEGY_EXPERIMENT`); the database must be chosen with
  `--confirm-disposable DO` (`REFUSED_THE_DATABASE_WAS_NOT_CONFIRMED_DISPOSABLE`);
  a database holding any position under a strategy experiment is refused
  (`REFUSED_THE_DATABASE_HOLDS_A_STRATEGY_BOOK`). Every run carries a run id
  stamped into the rows it writes. A regression pins all of it **at the
  consumers** — entry evidence, its inventory, per-lane P&L, the exposure the
  rail measures, the calibration sample.

**Re-measured after isolation** (`cap6`, a database that held no strategy
book; `research/evidence/BETTOR_ORDER_LIFECYCLE_CAPACITY.json`):

| phase | measured |
|---|---|
| sustained entry writes | 500 in 1.47 s = **340.14/s**, p50 2.69 ms, p99 7.66 ms |
| **complete lifecycles** | **120 attempted, 120 completed (rate 1.0)**, 12.36 s, 9.71/s, p50 **102.11 ms**, p90 122.74, p99 140.31, max 164.68 |
| per lifecycle | median 7 management cycles, 17 writes |
| accounting | 24,000 contracts, fees **$384.00 against $384.00 expected**, buys equal sells, **0 discrepancies**, 0 errors |
| duplicate delivery | the same lifecycles re-delivered: positions, orders, fills, decisions, fees, quantities **all unchanged** |
| **actual process restart** | three distinct OS processes: parent **1757**, first writer **1790** SIGKILLed (rc −9) after 5 completions, second writer **1793** finished the batch → exactly 15 positions, **no duplicate**, all flat, fees reconciled |
| writer microbenchmark totals | 650 positions / 650 orders / 650 fills, $1,040.00 fees against $1,040.00 expected |

Processing capacity is **not** opportunity, and the per-day figure in the
evidence file is labelled an extrapolation. Every row the harness writes is
in `CAPACITY_PROBE_WRITER_V1` or `CAPACITY_PROBE_LIFECYCLE_V1`.

## One completed scheduled cycle, and the actual blockers

Production, build `fae8fff`, arm state `ARMED_CONFIRMED`:

- cycle label **`EVALUATED_3_CANDIDATES`**
- verdict **`NO_TRADE`**, admissible **0**
- autonomous strategy inventory: **0 positions** in
  `AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW`; `entry_evidence` inventory 0
- books on the desk, never summed: `ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY` 3,
  `CONTROLLED_DEMONSTRATION` 1, `UNCLASSIFIED` 36 — none counts as strategy
  performance
- candidates stopped at, by gate: `VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE`
  24, `QUOTE_STALE` 19, `VENUE_BOOK_READ_RETURNED_ERROR` 4,
  `NO_VENUE_NATIVE_CONTRACT_IN_PREMAP` 2, `VENUE_QUOTE_STALE` 2,
  `VENUE_BOOK_READ_FAILED` 2

## What still prevents funded activation

Computed by the server, returned with every activation request, and split by
**who can clear it**.

### Remaining engineering — mine, and none of it blocks the panel

| item | state |
|---|---|
| `MAX_EVENT_EXPOSURE` cannot see held positions on the same event | `OPEN_BOOK_SQL` selects no event key. Open, named, unfixed |
| the desk's `UNCLASSIFIED` book (36 positions) | pre-existing provenance that predates the declared values |
| `us_premap` persists `sportsMarketType` only, not `sportsMarketTypeV2` | open |
| the three P&L review-pin tests | open; see the section below |
| `OPERATOR_PASSWORD` is not yet set in the service environment | one environment variable on the service. Until it is set, the control sign-in refuses by name (`OPERATOR_PASSWORD_NOT_CONFIGURED`) and the controls stay reachable to ops tooling server-side. **Not a secret to be typed into a chat** |

### Decisions only you can make

1. **The funded account.** The exact `account_id` in
   `bettor_desk_accounts` that may be armed, its venue, and your approval of
   it. The accounting-uncertain account is refused **by its row** and stays
   paused; renaming it changes nothing.
2. **The approved limits** — capital, per order, maximum exposure, daily loss
   stop. These are now **consumed**: approval tightens the frozen rails
   element-wise and can never loosen one. Approving takes the service
   credential, deliberately, because it is your act and not the desk's.
3. **Whether a FUNDED venue may be authorised at all.** A TEST-class venue
   is authorisable from the panel today. A funded one needs your written
   authorisation, and **enabling real submission is a code change** with that
   authority behind it — not a form.

### Evidence that has to arrive, from outside this repository

4. **venue book freshness basis** — what `marketData.transactTime` denotes is
   not established, so the explicit refusal stands and the 30 s limits are
   unmoved. UNKNOWN blocks.
5. **per-fixture settlement compatibility** — from the venue's own prose.
   UNKNOWN blocks.
6. **market scope metadata** — the scope token sets remain provisional until
   the published Sports Schema is in hand.
7. **an autonomous entry admitted on current markets, unwaived** — 0 so far.
   Research-waived and demonstration activity are excluded by construction.

Items 4–7 are **derived from rows**, not asserted: when the lane records the
evidence, the checks turn true without a code change; while it does not,
they read UNKNOWN and refuse.

## Open, and not claimed as clean

- **Three tests** in `tests/test_pnl_l34_review_pins.py` —
  `test_r_defect_h1_the_venue_fresher_than_the_fills_is_refused_drift_not_sized`,
  `test_r_defect_m1_the_deploy_seed_admits_the_landed_rules_unwitnessed_reduce`,
  `test_r_defect_m2_the_fills_axis_override_also_sizes_an_increase_past_the_fresh_reading`
  — **are open and this gate is not called clean.** What is now established,
  and what is not:

  **They are not attributable to this release.** The same nine full-suite
  runs, all with identical collection order (`-p no:randomly`), split by
  **wall-clock duration**, not by commit:

  | run | commit under test | duration | total failed | these three |
  |---|---|---|---|---|
  | GATE_BASE | baseline `6d75275` | 469 s | 177 | **present** |
  | GATE_BASE2 | baseline `6d75275` | 435 s | 173 | absent |
  | GATE_BASE4 | baseline `6d75275` | 415 s | 174 | absent |
  | GATE_BASE7 | baseline `6d75275` | 414 s | 174 | absent |
  | GATE_REL4 | this branch `fae8fff` | 460 s | 177 | **present** |

  The **baseline itself** produces 177-with-the-three on a slow run and
  174-without on a fast one. So the 177-vs-174 delta is not a difference
  between the two commits: it is a difference between a slow run and a fast
  run of the same code. The earlier reading — that the delta was
  "unattributed" — understated this; it is attributable to run conditions,
  and not to the diff.

  **The mechanism, now reproduced.** The file imports the mirror lane's
  fakes, which capture `NOW = time.time()` **at collection**, while a path
  under test reads the **real** clock when the test runs. A one-file
  reproducer injects exactly that gap and nothing else — a
  `pytest_collection_finish` hook that sleeps, committed as
  `backend/tools/pnl_clock_gap_plugin.py`:

  ```
  cd backend
  RN1X_TEST_DSN=... DATABASE_URL=... ADMIN_TOKEN=t PNL_REPRO_DELAY_S=300 \
    python3 -m pytest tests/test_pnl_l34_review_pins.py -q -p no:randomly \
            -p tools.pnl_clock_gap_plugin
  ```

  | gap between collection and execution | result |
  |---|---|
  | 0 s | 10 passed |
  | 120 s | 10 passed |
  | 240 s | 10 passed |
  | **270 s** | **3 failed** — h1, m1, the fast-tick drift pin |
  | **300 s** | **5 failed** — those, plus m2 and the per-market read |

  The failing set **grows with the gap**, and the three the gate reports are
  a subset of the 300 s set. Two further results fix the mechanism: importing
  all 536 modules while running **only** the three suspects gives **3
  passed**, so it is not an import-time effect; and running the whole
  339-file prefix reaches the file at **243 s elapsed** with **0** of them
  failing — just inside the threshold. In a 460 s suite the file runs about
  300 s in; in a 415 s suite, sooner. That is the duration correlation,
  explained.

  **And the cause is a real-clock read, not flaky tests.** The reproducer's
  second knob holds `time.time()` at its collection-time value while keeping
  the same gap. Same file, same 320 s:

  | 320 s gap | result |
  |---|---|
  | real clock running (`PNL_REPRO_FREEZE=0`) | **5 failed**, 5 passed |
  | real clock held still (`PNL_REPRO_FREEZE=1`) | **10 passed** |

  Freezing the wall clock cannot affect a path that uses the `now_ts` its
  tick was handed. It changed the outcome, so **some path under test reads
  `time.time()` directly** — a clock-discipline defect in the mirror lane,
  of which these test failures are the symptom.

  **Stated plainly:** pre-existing, in a lane this delivery does not touch,
  identical on the baseline, and **still open — nothing in it has been
  fixed.** No evidence in this record depends on that file.

## One more gate failure, and why it is arithmetic and not a regression

An earlier baseline run reported **177** where the runs today report **178**.
The extra identity is
`tests/test_run834_retention_and_races.py::test_race_update_one_microsecond_before_receipt_is_the_zero_ms_state`,
and it is **not caused by this release**: it fails *standalone* in all three
frozen checkouts, the baseline included, and it is in the baseline's own
same-day gate set.

The cause is float64 resolution. The test reads `clock.now()` — whose
`monotonic` field is `time.monotonic()`, i.e. container uptime — subtracts
one microsecond, and asserts the difference is `0.001 ms ± 1e-9`. The ulp of
a double doubles at 2¹⁶ = 65,536 s:

| monotonic | ulp | measured | error vs 0.001 ms | verdict |
|---|---|---|---|---|
| 40,000 s | 7.276e-12 | 0.001000000339 | 3.4e-10 | passes |
| 60,000 s | 7.276e-12 | 0.001000000339 | 3.4e-10 | passes |
| **66,995 s** | **1.455e-11** | 0.000999993063 | **6.9e-9** | **fails** |

This container crossed 65,536 s (18.20 h) of uptime during the session and
is at 18.61 h, which is why sixteen earlier gate runs passed this test and
this one did not. **The fix is one line in the test** — scale the tolerance
to `math.ulp(base.monotonic)`, or build the `Instant` from a small fixed base
instead of the live monotonic clock. It is **not changed here**: it is an
assertion in a lane this delivery does not touch, and you should know it is
an arithmetic boundary rather than a regression, because it will appear in
every gate run on a long-lived machine from now on.

## Open, and not claimed as clean (continued)

- `MAX_EVENT_EXPOSURE` still cannot see held positions on the same event
  (`OPEN_BOOK_SQL` selects no event key).
- `render-ops.yml` is 422 bytes under GitHub's 512,000-byte workflow ceiling.
- `us_premap` persists `sportsMarketType` only, not `sportsMarketTypeV2`.
- The desk's `UNCLASSIFIED` book (36 positions) is pre-existing provenance
  that predates the declared values.
- The desk is served from the API origin only. **Netlify publication remains
  separate** — this repository holds no Netlify credential, and you said that
  can stay separate for this delivery.
