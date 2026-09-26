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

## Readiness: two reproduced false passes, a third of the same family, and one coherent chain

### The counterexamples, each reproduced and pinned

**1 · `_freshness_check` passed on a clock that was never provided.** It asked
whether the basis token contained `UNESTABLISHED`, `NOT_RECORDED` or
`UNKNOWN`, and counted everything else as established. The lane's own word for
"the venue sent no transact time at all" is `VENUE_CLOCK_NOT_PROVIDED`, which
contains none of those.

| basis token in the last cycle's ledger | before | now |
|---|---|---|
| `VENUE_CLOCK_NOT_PROVIDED` | **met (false pass)** | **False** |
| `VENUE_CLOCK_UNPARSEABLE` | met (false pass) | **False** |
| `VENUE_TRANSACT_TIME` | met | met |
| `VENUE_TRANSACT_TIME` with **no** measured age | met (false pass) | **UNKNOWN** |
| `VENUE_TRANSACT_TIME_ESTABLISHED` — a token **no writer emits** | met (false pass) | **UNKNOWN** |

The vocabulary is now enumerated, an established basis must also carry a
measured age, and an unrecognised token is UNKNOWN — not a pass and not a
fail. A test asserts the vocabulary against
`workers/ext_pinnacle_loop`, the module that emits it, so it cannot drift back
into a substring guess. The invented token came from my own earlier fixture,
and the old check passed on it.

**2 · `_settlement_check` could not see a conflict.** It counted conflicts by
looking for `CONFLICT`; the settlement module's word is `INCOMPATIBLE`, which
contains no such substring.

| window | before | now |
|---|---|---|
| one `COMPATIBLE` | met | met |
| one `COMPATIBLE` **+ one `INCOMPATIBLE`** | **met, reporting "none conflicted" (false pass)** | **False** — "1 of 2 recent candidates are INCOMPATIBLE" |
| a verdict outside the enum | counted as neither | **UNKNOWN** |

The three verdicts are imported from `bettor_settlement_terms`. One conflict in
the window refuses, whatever else is compatible.

**3 · A third of the same family, found while fixing those.** The admission
check used `risk_verdict::text LIKE '%research_waiver%'`. The lane writes the
waiver **record** on every verdict, so the predicate matched **every row**:
every admission counted as waived and the check could never be satisfied by
anything. It failed closed — it granted nothing it should not have — but it was
unsatisfiable, and it reported "0 admitted without a waiver" about rows where
nothing had been waived. The test is now the waiver's own content
(`jsonb_array_length(... -> 'waived') > 0`).

### What "ready" means, and the coherent chain

Each per-kind check reads its own rows, so all four could be met while **no
single market carried the whole chain** — freshness on market A, settlement on
B, scope on C, admission on D. That is not an opportunity, and qualification
must not be assembled that way. A composite check now requires every link on
**one row** of `external_valuations`, which is where the lane records all of
them, including the venue clock basis and its measured age inside
`risk_verdict -> 'freshness_evidence'`:

- `admissible`, `decision = 'BUY'`
- no waiver consumed
- `settlement_comparison->>'verdict' = 'COMPATIBLE'`
- `period` in FULL_GAME / FULL_TIME / FULL_MATCH
- a supported established venue clock basis **and** its measured age

When one exists it is **named** — slug, condition id and each link's value.
The four per-kind checks are labelled diagnostics. `readiness()` says so in
`what_ready_means`, and a test seeds four markets that each satisfy a different
link and asserts the composite refuses.

## The authorization boundary, stated accurately

**The funded branch no longer refuses blind.** It returned the same refusal
whether or not the owner's authorisation existed, so the record could be
present and correct and nothing would change — "requires the owner's written
authorisation" while holding it.

| owner authorisation record | result |
|---|---|
| absent | `FUNDED_ACTIVATION_REQUIRES_THE_OWNERS_WRITTEN_AUTHORIZATION`, and it says the record is absent |
| names a different account | `THE_OWNERS_AUTHORIZATION_NAMES_A_DIFFERENT_ACCOUNT` |
| names a different venue | `THE_OWNERS_AUTHORIZATION_NAMES_A_DIFFERENT_VENUE` |
| covers a different limit set | `THE_OWNERS_AUTHORIZATION_COVERS_DIFFERENT_LIMITS` |
| matches account, venue and the effective-limit digest | **accepted** — verdict `AUTHORIZED_FOR_A_FUNDED_VENUE_SUBMISSION_STILL_DISABLED_IN_CODE` |

**And an executor consumes it.** Recording an authorization proves nothing; a
record nothing reads is a note in a table. `bettor_entry_execution.authorize_submission`
is the execution-side gate, and the proof of consumption is that its answer
*changes*:

| what the gate is given | refusal | `authorization_consumed` |
|---|---|---|
| no record | `NO_SUBMISSION_AUTHORIZATION_HAS_BEEN_RECORDED` | false |
| a record for another account | `THE_AUTHORIZATION_NAMES_A_DIFFERENT_ACCOUNT` | false |
| a record for another venue | `THE_AUTHORIZATION_NAMES_A_DIFFERENT_VENUE` | false |
| a record granted against different limits | `THE_AUTHORIZATION_DOES_NOT_COVER_THESE_EFFECTIVE_LIMITS` | false |
| a record with no digest | `THE_AUTHORIZATION_CARRIES_NO_EFFECTIVE_LIMIT_DIGEST` | false |
| **the matching record** | **`REAL_ORDER_SUBMISSION_IS_DISABLED_IN_CODE`** | **true** |

`authorize()` calls the gate immediately after recording, so the response
carries the **executor's** answer under `execution_boundary`, not the panel's.
An injected submitter is driven through the gate with a **TEST venue** and a
valid authorization and sends nothing, and a test asserts that
`order_submitted = True` appears **nowhere** in the package — so this gate is
not one path among many. `submitted` is false on every path, and
`authorises_capital` stays false.

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

---

# The four incomplete corrections in `0899264`, closed in `7dedc5a`

Your source review found four of the previous commit's fixes did not hold.
Each is reproduced as a counterexample first, then closed.

## 1 · A missing fill id silently lost executions

`fill_id_for()` answered `tvf:<order>:NO_VENUE_FILL_ID` for **every**
unidentified fill on an order — one id for all of them. With
`ON CONFLICT (fill_id) DO NOTHING` the first was written and every later
**distinct** execution came back as *already held*. Two fills became one, and
the quantity, the fees and the cash were all short.

**A content hash is not an identity either.** Two separate executions on a
resting order can share a quantity and a price exactly, so hashing them
collides for the same reason with extra steps. There is therefore no
substitute identity, and the code does not invent one:

| input | `fill_id_for` |
|---|---|
| `venue_fill_id="sim-abc:f1"` | `tvf:sim-abc:sim-abc:f1` |
| `None`, `""`, `"  "` | **`None`** |

A fill with no durable identity is **not ingested and not called already
held**. It becomes an explicit unresolved reconciliation state in
`ingestion_state[bettor_test_venue_unresolved_executions]`, and
`reconcile()` reports each one as an `UNRESOLVED_EXECUTION_IDENTITY`
discrepancy with `reconciles_exactly_once: false`. A visible problem instead
of a quantity that is quietly wrong.

Live, against Postgres — two executions of the same size at the same price:

```
   two unidentified executions, same qty and price
   written 0 | already_held 0 | UNRESOLVED 2
   refusal       THE_VENUE_SUPPLIED_NO_DURABLE_EXECUTION_IDENTITY
   ledger qty    40.0 (unchanged: nothing was ingested)
```

And the same fill, properly identified, handed back ten times:

```
   delivery  1 -> written 0 already_held 1 unresolved 0 ledger qty 40.0
   delivery  2 -> written 0 already_held 1 unresolved 0 ledger qty 40.0
   delivery 10 -> written 0 already_held 1 unresolved 0 ledger qty 40.0
   fill ledger rows after 10 deliveries: 1 (one execution)
```

Pinned by `test_two_unidentified_fills_are_both_unresolved_not_one_ingested`.

## 2 · Servicing did not establish ownership of the orders it selected

`check_servicing()` read the single global authorization record while
`_open_orders()` selected **every** open order in the experiment, with no
account or venue filter. Replacing account A's authorization with B's
therefore made A's orders invisible to A — stranded — and reachable from B's
servicing path.

Ownership is a property of the **order**, not of a replaceable record. It is
now read off the order's own position row (`source_account`, `venue` — the
position stores the real venue rather than a hard-coded literal) and the
selection is scoped by it. `check_servicing` does not consult the
authorization record for ownership at all; it checks the venue class and
reports, separately and for the record, whether new exposure would currently
be authorised.

Live, with both accounts holding open orders and **B's grant in the record**:

```
   A sees         ['tvx-6fb3b22892c548'] owner {'acct-pilot-test-001'}
   B sees         ['tvx-74ee2d41b0eb4a'] owner {'acct-pilot-test-002'}
   disjoint       True
   A can service  True | new exposure authorised for A: False
                        | THE_AUTHORIZATION_NAMES_A_DIFFERENT_ACCOUNT
   A NEW exposure THE_AUTHORIZATION_NAMES_A_DIFFERENT_ACCOUNT | ok False
   A polls        ['tvx-6fb3b22892c548'] | refusal None
   B polls        ['tvx-74ee2d41b0eb4a'] | refusal None
   B cancels      ['tvx-74ee2d41b0eb4a'] -- and A's order is not among them: True
```

So servicing survives **replacement** as well as expiry and revocation, and
one account's cancel cannot reach another's order. Pinned by
`test_two_accounts_each_service_only_their_own_orders`.

## 3 · Recovery still invented a cancellation

The line was

```python
seen_state = got.get("state") or dk.CANCELLED
```

so an order absent from the venue's open list whose status could not be read
was written `CANCELLED`. Absence plus an unreadable status is precisely the
case where we do **not** know, and a terminal state there reports flat while
the exposure may still be live.

`recover()` now adopts only a state this executor recognises
(`_KNOWN_STATES` = the open plus terminal vocabulary). Anything else — `None`,
a venue's own "unknown", a spelling we do not hold — leaves the row
**untouched** and appends the order to `unresolved`:

```
   case           OPEN_HERE_STATUS_UNREADABLE_AT_THE_VENUE
   venue said     None | exposure PRESERVED
   state before/after: PARTIALLY_FILLED -> PARTIALLY_FILLED | qty 40.0 -> 40.0
   reconciled[]   0 (nothing was resolved)
   terminal?      False
```

Only when the venue speaks again is it resolved — and the quantity still comes
from the ledger, not from the venue's running total:

```
   case           OPEN_AT_BOTH | venue state PARTIALLY_FILLED
   fills missed   1 | adopted qty 50.0 | from the ledger, after ingesting every fill
```

Pinned by `test_an_unreadable_status_is_not_a_cancellation`.

## 4 · The deployment guard failed open

S2a stopped only when it read the exact string `yes`, so a missing value, an
unexpected response or a failed read all fell through to the password write —
the write that creates a tracked-branch deploy. The order is now:

1. **Validate the release SHA** (40 hex) **before anything is mutated.**
2. `PATCH autoDeploy=no`, and require a **2xx on the update**.
3. Read the service back, and require a **2xx on the read**.
4. Require the read-back value to be an **explicit member of
   `DISABLED_VALUES`**. `__ABSENT__` (the key is not in the response) and
   `__UNREADABLE__` (the response would not parse) both stop the run.
   *Unknown is treated as enabled.*
5. Only then is the secret written.

S3b likewise requires the deploy list's own HTTP to be 2xx and `PENDING` to
parse; a failed or unparseable read is not "no pending deploys", it is no
answer, and it fails the step.

Pinned by `test_the_deployment_guard_stops_on_an_unknown_readback`, which
asserts the ordering and the allow-list in the workflow source.

**Why this matters here, measured today:**

```
sportsassets-api      srv-d9gcv6urnols73ce6er0  branch=claude/session-njaewf  autoDeploy=yes
sportsassets-workers  srv-d9gcv6urnols73ce6erg  branch=claude/session-njaewf  autoDeploy=yes
```

Both services track the protected default branch with auto-deploy on, which
is exactly the configuration that turns an env write into an unreviewed
publish.

## 5 · And the step that proves operator sign-in had the same shape

S4 is the one step whose entire purpose is to show that ordinary password
sign-in works and that an authorised shadow control action goes through. It
**printed** a bad sign-in and carried on, so a run could finish green while
the password was rejected, the cookie was never set, or the control action
never completed — and a green run is what gets reported as "operator access is
configured". It now stops on: a non-200 sign-in; no `bt_control` in the jar; a
minted `scope` that is not `control`; a pause or resume that is not 200; and a
control cookie that reaches `/api/admin` with anything but 401/403.

`git diff 7dedc5a..7349772 -- backend/` is empty: this correction touches the
workflow only.

# What the gate found, in my own delivery

## The harness, not the release: a stalled run

A first gate run of `7dedc5a` stopped moving at 38% and produced **no
summary**. A `py-spy` dump read like a deadlock — main thread idle in
`selectors.select` inside `asyncio.run`, only the event loop's own self-pipe
open. It was neither a deadlock nor the release.

Postgres folds an unquoted `CREATE DATABASE gateEdb` to `gateedb`, while a
connection string carries the name **verbatim**. My harness was invoked with
mixed-case names, so the migrations silently did nothing and every DB-backed
test met a connect error that `sportsassets/db.py` retries with **doubling**
backoff. The stall was a sleep in a retry loop.

| DSN | same checkout, same test |
|---|---|
| `…/gateEdb` | 4 failed in 62.95 s — `database "gateEdb" does not exist; retry in 1s/2s/4s/8s` |
| `…/deskdb` | **4 passed in 1.22 s** |

The harness now lower-cases the name for both the `CREATE` and the DSN, and
runs with `--timeout=180 --timeout-method=signal`, so a future stall is one
**named** failure with a stack rather than a run with no summary.
Recorded in `research/evidence/gate/GATE_HARNESS_DEFECT_MIXED_CASE_DB.json`.

## Two fixtures narrowed the provenance vocabulary

The first valid matched pair gave **178** identities for `6d75275` and **188**
for `7dedc5a`. Every one of the ten new ones was in my own executor test file,
and every one was

```
CheckViolationError: new row for relation "rn1x_positions"
violates check constraint "rn1x_provenance_declared"
```

They passed alone and in their group, and failed only in the full suite —
which is the signature of a fixture in another file.

`rn1x_provenance_declared` is dropped and re-added by four migrations, each
widening the vocabulary: 113 declares it, 117 re-declares three origins, 122
adds `UNCALIBRATED_RESEARCH_SHADOW`, 123 adds
`TEST_VENUE_EXECUTION_LIFECYCLE`. `scripts/migrate.py` keys on filename and
applies them in order, so the **deployed** schema always holds the widest set.
**Production was never affected.** But a fixture that *replays* migration
files into the shared test database does not get that for free, and two
stopped early:

| fixture | replayed | effect on everything that ran after it |
|---|---|---|
| `test_the_entry_lane_reaches_inventory.py` | 117, 122 | forbade `TEST_VENUE_EXECUTION_LIFECYCLE` |
| `test_the_source_calibration_is_measured.py` (two sites) | 117 | forbade `UNCALIBRATED_RESEARCH_SHADOW` too |

The first file's own comment documents this exact trap being closed for 122;
it re-opened when 123 landed. The second I did not know about — the guard
found it.

Both now replay 123 (and the calibration file 122), and
`test_no_fixture_reverts_the_provenance_vocabulary` closes the **class**: it
scans every migration that re-declares the constraint, takes the newest, and
fails any test file that replays one of them without also replaying the
newest. The third recurrence will be a failure in the file that causes it.

Verified on a database migrated from scratch, in suite order —
entry_lane, executor, source_calibration → **56 passed**.

## The instrument is stable, and that is why the diff means something

Today's baseline run is **byte-identical** to the list recorded two hours
earlier: the same 178 identities, on a different database, under concurrency,
with the `--timeout` plugin added. A failure-identity diff on this suite is a
real signal. A failure **count** is not, and this record never treats one as
if it were.

# The release, and production read back

`47ad3ce` was gated, then deployed API-only **by commit id** and read back.

## Operator access, configured and exercised

| step | result |
|---|---|
| S1 before | `HTTP 503` · `OPERATOR_PASSWORD_NOT_CONFIGURED` — genuinely unset |
| S2a | `before autoDeploy=yes branch=claude/session-njaewf` → `PATCH 200` → read back `'no'` → `confirmed disabled yes` |
| S2 | 32 characters generated **on the runner**, `PUT OPERATOR_PASSWORD HTTP 200` |
| S2b | `deploy 47ad3ce by id HTTP 201` |
| S3 | serving, probe **503 → 401** |
| S3b | deploy list `HTTP 200`, `live 47ad3ce`, **pending deploys for another commit: 0** |
| S4 | **password sign-in HTTP 200**, `scope control`, cookie set, `ttl_s 1800` |
| S4 | pause `200` → server readback `{"armed":false}` → `research lane now false` |
| S4 | resume `200` → `armed_confirmed true`, `funded submission DISABLED` |
| S4 | control cookie on `/api/admin` → **401** |
| S5 | password in the run artifact only, masked in every log line, never a workflow input |

The fail-closed S2a guard was exercised against the real service: auto-deploy
was genuinely `yes` and had to be confirmed off before the secret was written.
It is left off deliberately; releases go out by commit id.

## The desk, off the build that is serving

```
build           47ad3cebac6b18bab5143d847882d11f1aa13f60
funded          DISABLED
verdict         NO_TRADE          admissible 0
BOOK ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY  positions 4   strategy performance false
BOOK CONTROLLED_DEMONSTRATION            positions 1   strategy performance false
BOOK UNCLASSIFIED                        positions 35  strategy performance false
research lane armed     true
funded executor paused  true
orders this lane submitted 0
```

Three books, listed separately, never summed.

## The current-market cycle, candidate by candidate

The autonomous entry lane against live markets, 2026-09-26 22:10 UTC:

```
candidates 63   admissible 0
first failing stage:  1_PROBABILITY 24   5_EXECUTION_ESTIMATE 33   7_RISK 6
reached_execution_estimate 6    walk_took_levels 6
positive_edge 6   negative_edge_without_a_walk 33   edge_not_computed 24
execution_mode CROSSING_THE_ASK_AT_THE_TAKER_FEE
excludes       ANY_PASSIVE_OR_MAKER_FILL_ASSUMPTION
```

And the desk's own refusal census, 51 candidates:

| blocked at | candidates | what it is |
|---|---|---|
| `VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE` | 22 | a **stated** payout conflict — a decision on evidence |
| `QUOTE_STALE` | 19 | the clock was read and the price was too old — a decision |
| `VENUE_QUOTE_STALE` | 6 | same, on the venue side — a decision |
| `VOID_ABANDONMENT_BOOK_RULE_NOT_HELD` | 1 | we do not hold the bookmaker's rule — **an inability** |
| `NO_VENUE_NATIVE_CONTRACT_IN_PREMAP` | 2 | theirs |
| `VENUE_BOOK_READ_FAILED` | 1 | theirs |

Classified:

```
candidates 51   admitted 0
evaluated_to_a_judgement 47      could_not_be_evaluated 4
counts  DECIDED 47 · COULD_NOT_EVALUATE 1 · EXTERNAL_DEPENDENCY 3
verdict NO_OPPORTUNITY_AMONG_THOSE_EVALUATED_WITH_SOME_NOT_EVALUABLE
```

**47 of 51 were judged on evidence the lane held and declined — that part of
the engine worked. 4 could not be evaluated at all, and those say nothing
about opportunity.** Neither number alone is the honest headline.

### And this readback found one more gap

On first classification the verdict was **`REFUSAL_CLASSIFICATION_HAS_DRIFTED`**:
`VOID_ABANDONMENT_BOOK_RULE_NOT_HELD` was a live production refusal that the
evaluability table did not hold. That is the mechanism working — an unknown
code is reported as drift rather than folded into "no opportunity" or blamed
on somebody else.

It is **our** gap, not a conflict: `bettor_venue_settlement` found the venue's
terms and not the bookmaker's, returning `established=False` with `EV_NONE`,
so the comparison could not be *made*. Classified `COULD_NOT_EVALUATE`, along
with `VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED` and
`DRAW_HANDLING_NOT_RECONCILED`, which were missing for the same reason.
Calling it a conflict would have flattered the funnel by one.

`test_every_settlement_refusal_this_lane_can_raise_is_classified` now holds
the whole vocabulary — and checks reachability rather than assuming it, which
turned up `PUSH_NOT_APPLICABLE_TO_H2H`: declared and never emitted. Named
here rather than quietly skipped.

## What production says still blocks funded activation

```
ok         false
refusal    ACCOUNT_ID_NOT_SUPPLIED
unmet      account_selected_and_clean, limits_recorded_and_complete,
           limits_approved_by_the_owner,
           approved_limits_tighten_the_enforced_rails,
           venue_book_freshness_basis, settlement_compatibility,
           an_autonomous_entry_was_admitted_unwaived,
           an_eligible_market_with_one_coherent_chain
```

Eight prerequisites, and the first four are **yours to supply**: no account is
bound in production, and no limit set has been recorded or approved there. The
implementation is complete and exercised end to end against the internal
simulator; what it does not have is a named account and approved rails.

## Not met, and pre-existing: the demonstration position

`verify` step 20 fails, on the **demonstration** book, and it failed the same
way before this release:

```
1_HELD_EXPOSURE    OK
2_VENUE_CONTRACT   OK
3_PROVIDER_FIXTURE OK
4_PROBABILITY      FAILED  QUOTE_STALE -- the quote is 34.6 s old against a 30 s limit
first_failing_link 4_PROBABILITY
acceptance NOT met: 0 complete decisions at 0 distinct instants (need 2 and 2)
```

Links 1-3 hold; the chain stops at the source's own 30 s freshness rule. The
refusal is measured, not assumed — but the acceptance criterion is **not met**
and this record does not claim otherwise.

# Final readback: `c6dbd89` serving, and the one decision that is yours

`c6dbd89` was gated (178 identities, identical to `6d75275`; 12,809 passed),
deployed API-only **by commit id**, and read back. The protected worker was
listed before and after the deploy and is unchanged: `f5d1c05 live`.

**A correction in the doing of it.** The first deploy dispatch carried a 40-hex
SHA I had expanded from the short form **without reading it** —
`c6dbd89e9d3d…` instead of `c6dbd898e59c…`. Render refused the unknown commit
and the run failed, so nothing was published and production kept serving
`47ad3ce`. The value was then taken from `git rev-parse` and the deploy
succeeded: `HTTP 201`, `dep-das4d660tbcc73dpkfi0`.

## The desk, off the build that is serving

```
build           c6dbd898e59ced8a7b291f9178b00139eb1cefe2
as of           2026-09-26T22:44:07Z
funded          DISABLED          verdict NO_TRADE      admissible 0
BOOK ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY  positions 4   strategy performance false
BOOK CONTROLLED_DEMONSTRATION            positions 1   strategy performance false
BOOK UNCLASSIFIED                        positions 35  strategy performance false
research lane armed     true
funded executor paused  true
orders this lane submitted 0
funded resume            HTTP 409  FUNDED_RESUME_IS_NOT_AVAILABLE_FROM_THE_DESK
control with no operator token  HTTP 401
```

## The cycle, on the corrected classifier

Entry lane, live markets: **66 candidates, 0 admissible** — first failing
stage `1_PROBABILITY` 29, `5_EXECUTION_ESTIMATE` 31, `7_RISK` 6;
`positive_edge` 6, `negative_edge_without_a_walk` 31, `edge_not_computed` 29,
`negative_edge_with_a_walk` 0.

Desk census, 49 candidates, and **no drift this time**:

```
QUOTE_STALE                                   25   decision
VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE 18   decision
VENUE_QUOTE_STALE                              2   decision
VOID_ABANDONMENT_BOOK_RULE_NOT_HELD            1   inability (ours)
NO_VENUE_NATIVE_CONTRACT_IN_PREMAP             2   theirs
VENUE_BOOK_READ_FAILED                         1   theirs

candidates 49   admitted 0
evaluated_to_a_judgement 45      could_not_be_evaluated 4
DECIDED 45 · COULD_NOT_EVALUATE 1 · EXTERNAL_DEPENDENCY 3
unclassified_codes []
verdict NO_OPPORTUNITY_AMONG_THOSE_EVALUATED_WITH_SOME_NOT_EVALUABLE
```

## THE DECISION THAT IS YOURS, and why it is now concrete

Production's account registry holds **one** row, and it is the paused one:

```
registry accounts        1
eligible by their rows   (none)
paused on their rows     acct_fc2d773a2afa4851
holds no balances        true
paused account by id  ok false  refusal ACCOUNT_IS_PAUSED
a name with no id     ok false  refusal ACCOUNT_ID_NOT_SUPPLIED
account recorded         -
limits recorded          {}
```

So there is **no eligible funded account in production**: the only registered
account is the accounting-uncertain one, and it is still paused by its own row
— refused by canonical id, under a different display name, exactly as it must
be.

The execution path is complete and exercised end to end: authorization →
submission → acknowledgement → partial fill → recovery → cancellation →
reconciled accounting, against an internal simulator, with expired, revoked,
mismatched-account and mismatched-venue authorization each refusing a new
submission while servicing continues. What it does not have is an account and
approved rails, and neither of those is mine to invent.

**Needed from the owner, to go further:**

1. **A named funded account** — either a new account id to register as
   ACTIVE with CLEAN accounting, or a decision to resolve the accounting on
   `acct_fc2d773a2afa4851` and unpause it. Until one exists, `authorize()`
   refuses at `account_selected_and_clean`, correctly.
2. **The approved limits**, as four numbers: capital, per-order, max
   exposure, daily-loss stop. Approvals may only TIGHTEN — the frozen rails
   digest `a19eb60a2817…` cannot be raised from here, and a proposal above a
   frozen rail is reduced to it element-wise.
3. **Final activation authorization**, in writing, naming that account and
   that limit set.

Funded submission stays **DISABLED in code** regardless of all three: enabling
it is a separate code change, and this record does not treat any of the above
as authorising a real-money order.
