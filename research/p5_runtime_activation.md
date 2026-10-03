# P5 at runtime: activating the institutional stream evidence and reading the verdict

Branch `claude/cand22-stream`, built on `337cf04` (the SHA production runs on
`claude/release-api`). This branch adds code only. It deploys nothing, changes
no env and places no orders.

## What the branch adds

| Piece | File | Runs in |
|---|---|---|
| Stream evidence recorder: listener on `ResidentBooks`, rows per symbol per minute | `backend/sportsassets/institutional_stream_evidence.py`, hooks in `institutional_stream.py` | workers, only when `INSTITUTIONAL_MD_STREAM=on` |
| Same-book probe (read-only): resident stream book vs retail public book, read within 1 s | `backend/sportsassets/institutional_same_book.py` | workers, only when the stream is on (kill switch `INSTITUTIONAL_SAME_BOOK_PROBE=off`) |
| Worker wiring: recorder attached before `start_default()`, flush every 60 s, probe every 60 s | `backend/sportsassets/workers/institutional_md.py` | workers |
| Tables `institutional_stream_evidence`, `institutional_same_book_probe` | `backend/migrations/210_institutional_stream_evidence.sql` (+ rollback) | applied by the migration runner at boot |
| P5 runtime evaluation | `backend/sportsassets/p5_runtime.py` | API |
| `GET /api/command/p5/evidence` (`require_command`, `Cache-Control: no-store`, optional `?symbol=`) | `backend/sportsassets/api/app.py` | API |

What gets recorded, per symbol per minute (plus one process row, `symbol='*'`):

- connection id and epoch, with connect and reconnect times
- the first complete book on the current epoch after the (re)connect
- gap events opened and closed in the minute
- venue `transact_time` against local receipt: receipt age at flush, plus the minute's skew min, median and max
- message, update and stray counts, and the longest gap between updates
- instrument state and its source
- the top 5 levels, scaled by the instrument's own scales
- the stream's own `current()` answer, with its full evidence, so the API can re-evaluate P5 on it

Every row is written from gRPC stream events. None of it comes from the REST poll, which keeps running and is still reported as `REST_POLL_MAINTAINED_IN_MEMORY`.

The same-book probe has no order path. It uses a keyless retail SDK client and calls only `markets.book`, which is `GET /v1/markets/{slug}/book`. Every transport the client can route through (the default transport and each proxy mount) is wrapped in two layers:

- the outer layer refuses any method other than GET or HEAD;
- the inner layer is the process write lock plus the pacing gate (`venue_request_gate.PacedTransport`).

A sample is compared only when all of these hold:

- `institutional_contract_map` maps the slug to the symbol EXACTly;
- both stream reads are current;
- the retail read parsed;
- the whole window is 1 s or less.

The table CHECKs `orders_placed = 0`.

## The endpoint's shape

```
GET /api/command/p5/evidence[?symbol=<slug>]
{
  "verdict": "LIVE_ADMISSIBLE" | "BLOCKED",
  "first_blocking":          {predicate, blocker, reason, action, depends_on} | null,
  "first_blocking_external": {predicate, blocker: "EXTERNAL", reason, action} | null,
  "internal_blockers": [...], "external_blockers": [...],
  "proven": [...], "not_proven": [...],
  "owner_approved": bool,
  "artifact": {rule_id, version, code_sha256, owner_approved, stored_status,
               stored_sha256_matches_code, owner_approved_at, owner_approval_actor,
               approved_live_book_rules},
  "deciding_process": {pmx_credential_names (booleans), pmx_missing (names),
               institutional_md_stream_flag_on, market_data_identity_guard,
               stream_state, stream_start, identity_mapper_installed,
               live_book_evidence_hook_installed, decision_price_source,
               c13_enforced_by_actual_lane, host, pid},
  "subject_symbol": ..., "decision_path_verdict": {...}, "per_symbol": {...},
  "predicates": [ {predicate, status: PROVEN|NOT_PROVEN,
                   blocker: EXTERNAL|INTERNAL|DEPENDENT|RUNTIME|null,
                   reason, action, depends_on, checks, deciding_process,
                   runtime_evidence}, ... ],   // C1..C13, A1, S1, in that order
  "runtime_evidence": {"stream": {status LIVE|STALE|ABSENT, processes, symbols
                         (each with worker_p5: P5 re-evaluated on the recorded
                         current() answer), aggregates},
                       "same_book": {status SUPPORTED|CONTRADICTED|INCONCLUSIVE|
                         UNTESTED|ABSENT, totals, by_symbol}},
  "rule": "P5_LIVE_STREAM_BOOK_V1", "rule_sha256": "...", "evaluated_at": ...
}
```

How each predicate is judged:

- **C1..C12:** taken from the decision path's own P5 evaluation in the API process (`live_book_evidence.evaluate_for`). That means the API's installed identity mapper, its stream reader and its price source. A book that is resident in the workers proves nothing about the API.
- **C2:** judged on the API's stream state. Its sub-checks, in order:
  1. PMX_* names present
  2. `INSTITUTIONAL_MD_STREAM` on
  3. identity guard passes
  4. stream started in this process
  5. venue accepts the credential
  6. stream running

  The first one that fails is the exact reason.
- **C5:** proven only when C3, C4 and C7 are proven. This is stricter than the rule's pass-through, because the rule itself says integrity "rests on" those three.
- **C13:** proven only when `ActualLane._run` calls `verdict_age_refusal`.
- **A1:** owner approval of this rule's hash on `live_rule_artifacts`.
- **S1, the same-book premise:** the rule hands this to the identity mapping (NE7 -> LB6), so it gets its own gate here, which adds to the rule and does not relax it. S1 is PROVEN only when, over 24 h, there are at least 30 comparable samples, the agreement rate is at least 95 %, and there is zero disagreement while the stream book was stable.

## Operations for the integrator (after deploying the integrated SHA to BOTH services)

Preconditions. The integrated SHA (call it `INT`) contains this branch.
It has been pushed to `claude/release-api` and deployed to both services:

- `deploy-api-commit` with arg=`INT` for sportsassets-api;
- `workers-commit-deploy` with arg=`INT` for sportsassets-workers, which has autoDeploy=no.

The `deploys` action shows `INT` live on both services. Migration 210 runs at API boot through `start.sh`.

### (a) Turn the stream on in the workers

**Ordering.** `env-set` makes Render redeploy sportsassets-workers from the head of its tracked branch, which is `claude/release-api`. It does not redeploy the commit that is running now. So before the env-set, confirm that `claude/release-api` head == `INT`:

```
git ls-remote origin refs/heads/claude/release-api   # must print INT
```

If head has moved past `INT`, the env-set ships that newer head to the workers. In that case either re-point first, or accept the newer head and re-verify with `deploys`.

Dispatch the workflow from `ref=claude/release-api`:

```
gh api repos/matthewtaylor141-lab/SportsAssets/actions/workflows/render-ops.yml/dispatches \
  -f ref=claude/release-api \
  -f 'inputs[action]=env-set' -f 'inputs[service]=sportsassets-workers' \
  -f 'inputs[arg]=INSTITUTIONAL_MD_STREAM=on' -f 'inputs[confirm]=DO'
```

Then verify:

- `render-ops` `deploys` on sportsassets-workers: the newest deploy is live at `INT`.
- `render-ops` `env-keys` on sportsassets-workers: lists `INSTITUTIONAL_MD_STREAM`. Only the keys are shown, never values.
- About 2 minutes after boot: the readback below shows process rows, and the workers heartbeat shows `stream.state` = `CONNECTED`.

Leave `INSTITUTIONAL_SAME_BOOK_PROBE` unset; it defaults to on. Its kill switch is `INSTITUTIONAL_SAME_BOOK_PROBE=off`. The stream's own kill switch is `INSTITUTIONAL_MD_STREAM=off`, or deleting the variable with `env-del`.

### (b) Production readback SQL

The readback is `research/p5_stream_evidence_readback.sql` on branch `claude/command-center`. It was committed there with `[skip render]`. It is read-only and passes the research-sql guard: no mutating keyword, and no meta-command other than `\echo`. Run it with:

```
gh api repos/matthewtaylor141-lab/SportsAssets/actions/workflows/research-sql.yml/dispatches \
  -f ref=claude/command-center -f 'inputs[file]=p5_stream_evidence_readback.sql'
```

Its sections:

0. Tables present and migration 210 applied.
1. Workers process rows: state, connection id and epoch, connects and reconnects, last disconnect, message counts, subscription errors, refusal.
2. Newest minute per symbol: `current()` answer, receipt age, skew, first complete book after (re)connect, open gap, state, top of book, exact identity.
3. Last 60 minutes per symbol: minutes current, minutes inside P5's 2 s bounds, connects, disconnects, gap events, updates, skew and receipt-age distribution, longest gap between updates.
4. Every gap event in the last 60 minutes.
5. Same-book verdicts per symbol over 24 h, and the S1 totals (5b).
6. The newest 20 same-book samples, with both books and the diff.
7. The P5 artifact row.
8. The workers heartbeat: stream digest, evidence counters and probe counters.

### (c) The P5 verdict

```
GET https://<api-host>/api/command/p5/evidence        (command cookie)
```

Read these fields:

- `verdict`
- `first_blocking_external`
- `internal_blockers`
- `owner_approved`
- `runtime_evidence.stream.status`, which should be `LIVE` once (a) has taken effect
- `runtime_evidence.same_book.status`

## Expected result while PMX_* is absent from the API

The verdict will be **BLOCKED**, with one exact external predicate:

```
first_blocking_external = {
  predicate: "C2_STREAM_RUNNING",
  reason:    "PMX_CREDENTIALS_ABSENT_FROM_DECIDING_PROCESS",
  action:    "owner: add the PMX_* names (PMX_CLIENT_ID, PMX_PARTICIPANT_ID,
              PMX_KEY_ID, PMX_PRIVATE_KEY_B64) to sportsassets-api in the Render
              dashboard (the values the workers service already holds)"
}
```

This is an owner action in the Render dashboard. The values must not go through `render-ops env-set`, which keeps them in the dispatch payload and prints their length.

Adding PMX_* to the API grants no order capability in code:

- `pmx_institutional` has an allow-list of three reads (instruments, bbo, book).
- `pmx.py` is the preprod adapter. It is host-guarded to `api.preprod.polymarketexchange.com` and reads `PMX_PRIVATE_KEY`, not `PMX_PRIVATE_KEY_B64`.

Note that the API process is not venue-write-locked the way the workers are.

### What else the endpoint will list (after the C1 / C2 / C12 commit)

The engineering gaps C1, C2 and C12 are closed in code; see "P5 in the deciding process" below. With today's env on sportsassets-api, the endpoint lists **no INTERNAL blocker**:

- **`first_blocking`** = `C1_IDENTITY_EXACT`, DEPENDENT on C2. The API's exact identity mapper is installed, but it answers only over refdata the API holds, and the API holds refdata only while its stream runs. C3..C12 are DEPENDENT on C2 for the same reason.
- **C2's later external sub-check:** `INSTITUTIONAL_MD_STREAM_NOT_ON_IN_DECIDING_PROCESS`. The flag must also be on for sportsassets-api, where the lifespan's `start()` reads it.
- **A1**, EXTERNAL `OWNER_APPROVAL_NOT_RECORDED_FOR_THIS_RULE_HASH`: the artifact is `READY_FOR_OWNER_APPROVAL`, sha256 `b2a49354...564a`. The owner approval action is in `research/p5_live_stream_book_v1.md`.
- **S1**, EXTERNAL `SAME_BOOK_PROBE_HAS_NO_COMPARABLE_SAMPLES` until (a) has run long enough. That needs at least 30 comparable samples in 24 h, at least 95 % agreement and zero disagreement while the stream book was stable. The probe takes one sample per EXACTly mapped focus symbol per minute. Its outcome is itself an external fact about the venue:
  - If no focus symbol maps EXACTly (only moneylines and exact one-to-one instruments do), every sample reads `NOT_COMPARABLE / IDENTITY_NOT_EXACT` and S1 stays UNTESTED.
  - If the retail and exchange books disagree while the stream book is stable, as the unresolved 2026-09-19 0.97/0.99 vs 0.59/0.60 side-by-side did, S1 is `SINGLE_BOOK_PREMISE_CONTRADICTED_BY_RECORDED_SAMPLES`. No action fixes that: it means the institutional book is not the retail executable price.
- **C13** is PROVEN: `ActualLane._run` enforces `verdict_age_refusal`, with a 2 s limit.

So LIVE_ADMISSIBLE needs exactly these four external predicates:

1. PMX_* names on sportsassets-api (owner, Render dashboard).
2. `INSTITUTIONAL_MD_STREAM=on` on sportsassets-api. Setting it redeploys the API from its tracked branch; the same ordering note as (a) applies.
3. A1 owner approval of the artifact.
4. S1 SUPPORTED on recorded samples.

## P5 in the deciding process (C1, C2, C12)

- **C2: the stream in the API.**
  - The API lifespan calls `institutional_api_stream.start()`, which is the workers' own `institutional_stream.start_default`.
  - It starts only when `INSTITUTIONAL_MD_STREAM=on` and the PMX_* credential passes the market-data identity guard in the API process. Otherwise it records `DISABLED_BY_CONFIGURATION` or `CREDENTIAL_REFUSED_BY_IDENTITY_GUARD` (`MARKET_DATA_CREDENTIAL_ABSENT`), exactly as the workers do, and starts no thread, no task and no venue call.
  - When it does start, one background task reads instrument refdata with the workers' `bootstrap_instrument`, which is the allow-listed `instruments` read, paced. It covers the workers' focus set plus the symbols the decision path asked about. Each read is repeated only when due: an hour for a listed symbol, 5 minutes for an unlisted one. The task then calls `set_instrument` and `want`.
  - API shutdown stops the task and the stream.
- **C1: the identity mapper.** `live_book_evidence.IDENTITY_MAPPER` = `institutional_api_stream.identity_mapper`. It answers EXACT only on an exact `institutional_contract_map` mapping (YES / long leg, symbol = slug) over the refdata record the API holds. Anything else is `None`, which fails closed.
- **C12: pricing the actual lane.**
  - `execution_intent.start` installs `decision_hooks.LIVE_BOOK_STREAM` = `live_book_evidence.observe`. That makes the decision's one identity answer and one resident stream read.
  - When the read is a current book of the exactly mapped symbol, and that symbol equals the slug, `paper_benchmark` prices the actual lane from that one observation. Book, IOC limit, depth, fees and EV all come from it, using the paper lane's own sizing and economics rules (same min edge, target, cap, fee function and 10 s book bound). The observation's id and age are recorded.
  - P5 is evaluated on the same read with `priced_from` = that observation, and the intent's wire, limit, quantity and receipt instant are the stream's. The REST pricing is kept beside it as `paper_rest_pricing`.
  - With no such book, `REST_PAPER_BOOK` stands and C12 refuses as before. A stale, crossed, gapped or other-symbol book refuses, and `Venue.place` is never called.
  - Paper simulation, thresholds, the 2 s bound, the $25 cap and the 1:1,000 scale are unchanged.
  - The admission accepts the exchange's explicit `INSTRUMENT_STATE_OPEN` as tradable, because the market state now comes from the same observation as the price.
- **Stream off (today's production): the decision path is unchanged.** The mapper holds no refdata and answers `None`, and the intent is exactly the REST path's. A test proves this through the real decision path.

## Tests

All run against fakes: in-process gRPC venue, fake retail reader, httpx MockTransport. The pg cases use `RN1X_TEST_DSN` and roll back.

- `backend/tests/test_institutional_stream_evidence.py`: evidence recorded from the real transport, then persisted and re-evaluated.
- `backend/tests/test_institutional_same_book.py`: agreement, disagreement, window, identity and retail failures; GET-only keyless client; `orders_placed` CHECK.
- `backend/tests/test_p5_runtime_evidence.py`, which covers:
  - today's API: BLOCKED, first external predicate = C2 PMX_* absent;
  - C2's sub-check order;
  - LIVE_ADMISSIBLE end to end against the in-process gRPC venue;
  - each fact flipped alone;
  - the endpoint.
- `backend/tests/test_institutional_md_orderless.py` §6 and §7: the new code paths, including the API start path, are structurally orderless.
- `backend/tests/test_p5_api_stream.py`: C1 mapper and C2 start, refdata, shutdown.
- `backend/tests/test_p5_c12_stream_priced_actual.py`, end to end through the real reactive decision path:
  - C12 proven with a current stream book, and one order at the stream price;
  - C12 refused with no stream book;
  - stale, crossed, gap, other-symbol and below-edge books refuse with zero placements;
  - stream off leaves the path unchanged.

All new files are in `backend/tools/capital_critical_tests.txt`.
