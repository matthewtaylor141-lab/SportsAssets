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
| account = "PMUS ACCOUNTING_UNCERTAIN" | **refused `THE_ACCOUNTING_UNCERTAIN_ACCOUNT_STAYS_PAUSED`**, nothing stored |
| activate | **409 `FUNDED_ACTIVATION_PREREQUISITES_NOT_MET`**, with the unmet list |
| any control with no operator token | 401; with a read cookie only, 403 |
| control state, as the rows read | research lane armed true, funded executor paused true, working modelled orders 3 |

Activation is refused **on the server**, computed from the stored control
rows — the button now sends, because a disabled button establishes nothing
when anybody can POST. The limits and account forms record **proposals**;
they change no enforced rail, and the enforced rails are shown beside them
with their digest.

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

Computed by the server and returned with every refused activation request:

1. **a named funded account** — none recorded
2. **that account approved by the owner** — approval is yours, not this panel's
3. **an approved limit set** — none recorded; and a recorded set is a
   proposal, never an enforced rail
4. **venue book freshness basis** — unresolved: what
   `marketData.transactTime` denotes is not established, so the explicit
   refusal stands and the 30 s limits are unmoved
5. **per-fixture settlement compatibility** — from the venue's own prose;
   UNKNOWN refuses
6. **market scope metadata** — the scope token sets remain provisional until
   the published Sports Schema is in hand
7. **an autonomous entry admitted on current markets** — 0 so far

## What I need from you

1. **The funded account decision** — the exact account (name, venue,
   identifier) that may be armed, and your approval of it. The
   ACCOUNTING_UNCERTAIN account is refused by name and stays paused.
2. **The approved limits** — capital, per order, maximum exposure, daily loss
   stop. Recording them is a proposal; making them enforced rails is a code
   change with your authorisation behind it.
3. **A Netlify credential**, only if you want the desk on
   `command.bettortoken.com` as well: an auth token and the site id. Without
   them the API-hosted URL above is the desk.

## Open, and not claimed as clean

- **Three tests** in `tests/test_pnl_l34_review_pins.py` fail in the
  full-suite gate on this branch and not on the baseline (177 vs 174; zero
  other differences, nothing newly passing). They are **not** explained by my
  product code: they still fail with `app.py` and `command_rn1x.py` reverted
  to the baseline and with my new test files removed, and they pass when the
  file runs alone, on a fresh database, on the post-suite database and in a
  four-file slice. The failing assertion is a `reduce_ref` clock seeded from
  `fills_clock`, which falls back to `now − 3000` when the fake holds no
  fill — cross-test coupling in a pre-existing review-pin file on the mirror
  lane, which this release does not touch. **Unattributed and open.**
- `MAX_EVENT_EXPOSURE` still cannot see held positions on the same event
  (`OPEN_BOOK_SQL` selects no event key).
- `render-ops.yml` is 422 bytes under GitHub's 512,000-byte workflow ceiling.
- `us_premap` persists `sportsMarketType` only, not `sportsMarketTypeV2`.
- The desk's `UNCLASSIFIED` book (36 positions) is pre-existing provenance
  that predates the declared values.
