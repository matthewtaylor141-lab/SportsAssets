# STAGE 2 — ONE APPROVAL REQUEST

**Frozen code SHA: `88d429ed056690d193a9c95402b0746703a7f0bd`**
Branch `claude/bettor-observation-repair` (no auto-deploy).
Production is `claude/session-njaewf` @ `691fa598`, which is an **ancestor**
of `88d429ed` — so this is a fast-forward, never a force.

This document covers **both** the staged deployment and the probe. It is
the only handoff; there is no further preparation step behind it.

Nothing here has been deployed. `BETTOR_LIVE_LOOP` is still `off`.
`obs-state` read **`false`** at `2026-09-22T01:04:14Z`
([run 35674390111](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35674390111)).
`obs-run` has not been set. No trading control has been touched.

---

## 1. What is being asked for

Two authorizations, in order, with a gate between them:

| | Action | Gate before proceeding |
|---|---|---|
| **2A** | Deploy `88d429ed`, set the probe environment, clear the environment stop — and stay **idle behind the database control** | The idle-deployment evidence in §5 |
| **2B** | Arm the budget, set `obs-run`, run one bounded probe, stop it | — |

2A is deliberately the more interesting half. It is the first time production
will be held still by the **database** control rather than the environment
variable — the exact gap the Stage 1 acceptance identified.

---

## 2. The two pending repairs in this package

**Retry-After.** The previous build capped the server's delay at 120 s, which
meant retrying **earlier than the server asked**. `PROBE_RETRY_AFTER_CAP_S` is
gone. `PROBE_SUSPEND_ABOVE_S = 120.0`: a `Retry-After` above it **abandons the
round** and carries the server's own delay through the loop backoff as a floor.
The delay is used verbatim — never shortened. (HTTP-date form is deliberately
not parsed; an unparsed `Retry-After` suspends rather than guesses.)

**Effective-configuration logging.** `effective_config()` reports what **this
process** sees in its own `os.environ`, and is logged on every refusal. It now
carries all eight approved limits; four of them (the budget, the deadline, the
listing bounds) were missing, which would have left them unverifiable from the
idle deployment. Six tests pin the line to the approved figures.

---

## 3. The configuration, exactly as authorized

| # | Setting | Value | Where it lives |
|---|---|---|---|
| 1 | `BETTOR_LIVE_LOOP` | `on` | worker env (cleared **by deploy** — see §7) |
| 2 | Observation control | **`false`** | `ingestion_state.bettor_live_observation` |
| 3 | Distinct candidates, **whole probe** | **40** | `ingestion_state.bettor_live_probe_state` |
| 4 | Enrichment rate / in flight | **0.25 req/s**, **1** | `BETTOR_PROBE_MAX_RPS`, `BETTOR_PROBE_CONCURRENCY` |
| 5 | Listing bounds, counted apart | 6 pages, 2 retries (2 s, 8 s), 20 s timeout | code |
| 6 | PMUS frames captured | **25** | `BETTOR_FRAME_CAPTURE_N` |
| 7 | `MAX_CONTRACTS` | **0** | `BETTOR_LIVE_MAX_CONTRACTS` (unset → `"0"`) |
| 8 | Probe deadline → auto-shutdown | **1800 s absolute**, then `disarm()` | budget row |

**40 is a lifetime total, not a per-round figure.** It is enforced by
`max_distinct`, applied *ahead* of the per-sweep clamp, and it is held in the
**database** rather than in memory — because `workers/all.py:run_forever` is a
`while True` with a 5 s restart delay, so any in-process cap is re-granted on
every cycle. Consumption is charged **before** use via an SQL-side increment;
if the write fails the loop refuses with `BUDGET_NOT_RECORDABLE` rather than
acquiring uncharged.

---

## 4. Total request bounds — for the entire probe

| Path | Bound | Arithmetic |
|---|---|---|
| Listing | **≤ 18 HTTP requests** | 6 pages × 3 attempts (1 + 2 retries) |
| Enrichment (BBO) | **≤ 40 distinct markets**, **≤ 160 attempts** | 40 × (1 + `PROBE_MAX_RETRIES` 3) |
| **Total HTTP** | **≤ 178 requests** | 18 + 160 |
| WebSocket | **1** connection, **≤ 1** subscribe message | ≤ 40 slugs, `SUB_BATCH` 100 |
| Frames retained | **≤ 25** | `BETTOR_FRAME_CAPTURE_N` |
| Orders | **0** | no submit/cancel/close reachable from the import closure, asserted over the source |

**Wall clock.** 40 attempts paced serially at 0.25 req/s is 39 × 4 s ≈ **156 s**;
the 160-attempt worst case is ≈ **636 s**. Both sit inside the 1800 s deadline.
The pacer bounds the rate requests are **started** at and is constructed per
round — with a 40-distinct lifetime budget there is effectively one round, so
0.25 req/s holds across the whole probe.

For comparison: the failed 21 September activation issued ~440 venue requests
in 16 minutes and was still accelerating. This probe's **ceiling** is 178.

### The rate limit is NOT ESTABLISHED

For this endpoint **and this credential context**. Stating it precisely,
because three weaker readings are all wrong:

- Across 65,980 archived responses there is **no** `RateLimit-*` or
  `Retry-After` header — the only headers are `date`, `content-type`,
  `cache-control`, `server`, `age` — and **zero** 429s. *Missing headers and
  zero observed 429s do not establish that no published limit exists.*
- The pattern that survived was strictly **serial**: the densest 1-second
  window across the entire archive is **exactly 1 request**, sustained
  0.030–0.285 req/s. That is an **existence proof**, not a boundary.
- The archive is **unauthenticated public-gateway** traffic. The worker is
  **authenticated**, so its limits are unestablished in *either* direction —
  they may be higher or lower.

0.25 req/s sits inside the demonstrated envelope. An override above it sets
`above_demonstrated_envelope: true` in the logged configuration, so it cannot
be raised quietly.

---

## 5. Stage 2A — the idle deployment, and the evidence required

Deploy `88d429ed`. Set the environment. **Leave `obs-state` at `false`.**

With the environment stop cleared, the loop reaches the **database** control
and refuses there. That refusal is the deliverable. Required, all four:

1. **The false control was actually read** — a `bettor_live_loop: not
   observing (STOPPED_BY_CONTROL: ...)` line. Not "the loop did not run":
   a line proving it asked the database and was told no.
2. **The worker refused observation** — and refusing is *free*: no listing
   page, no BBO read, no schema initialization, no socket, no credential read.
3. **The effective configuration matches §3** — the same line carries
   `effective_config()`, read from the process's own `os.environ`, with
   `max_rps: 0.25`, `concurrency: 1`, `frame_capture_n: 25`,
   `max_contracts: "0"`, `budget_max_distinct_default: 40`,
   `budget_deadline_s_default: 1800.0`, `suspend_above_s: 120.0`,
   `listing_max_retries: 2`.
4. **The bounded cadence** — the 300 s first backoff rung, not 5 s.

*Setting an environment variable does not prove the running process received
it.* That is why the verification is a log line from the process, and why the
budget figures in it are labelled `_default` with
`budget_authority: "the ingestion_state row, not this line"` — a refusal at
the control returns before the budget row is read, so the line must not claim
to have read it. A test fails if an unmarked budget figure ever appears.

Also verified at 2A: both services' running SHA, API `/healthz`, zero
observation-related venue requests, observation table counts unchanged, and
`MAX_CONTRACTS` / `live_trading_paused` / `mirror_live` / the execution gate
all read and unchanged.

---

## 6. Stage 2B — the probe

`obs-arm confirm=DO` (40 distinct, `now() + interval '30 minutes'`), then
`obs-run confirm=DO`. The running process picks it up within
`CONTROL_EVERY_S` = 30 s. No deploy.

| # | Objective | How it is settled |
|---|---|---|
| 1 | BBO reachability and **actual field parsing** | Real `marketData`-wrapped bodies parsed; `sharesTraded`, `bestBid`/`bestAsk`, `state` reported as parsed or not. A field that does not parse stays **ineligible** rather than guessed. |
| 2 | WebSocket connectivity and **real frames** | Up to 25 actual PMUS frames captured. `frame_capture_report()` returns `NOT_ESTABLISHED` for decoding, timestamps and book semantics until frames arrive, flags `DELTA_SUSPECTED` on thin repeats, and **never** promotes `ASSUMED_FULL_REPLACEMENT` on its own. |
| 3 | Fresh, **correctly labelled** observations and decisions | Durable rows, lane `polymarket-us/institutional/decision-only`. Coverage statuses (`PROBE_READ_FAILED`, `PROBE_NO_MARKET_DATA`, `PROBE_TIMED_OUT`, `PROBE_RATE_LIMITED`, `PROBE_SUSPENDED_BY_RETRY_AFTER`, `PROBE_STOPPED_BY_CONTROL`, `NOT_PROBED_YET`) are **coverage facts, never rule verdicts**, and are reported separately from economic eligibility. |
| 4 | **obs-stop during acquisition or streaming** | `obs-stop confirm=DO` issued mid-probe. Declared bound below. |
| 5 | **No further requests or records after shutdown** | Request count and the three observation table counts re-read after quiescence and compared. |

**Sequence within the probe** so all five are settled in one run: acquisition →
selection → subscription → durable decisions (1–3), then `obs-stop` during
streaming (4), then the quiescence check (5). The 1800 s deadline and automatic
`disarm()` are the backstop if anything hangs.

### The declared stop bound

New acquisition ceases within
`CONTROL_EVERY_S` (30 s) + `PROBE_STOP_CHECK_EVERY / max_rps` (5 / 0.25 = 20 s)
+ one timeout (8 s) = **58 s**, with a **90 s** ceiling to a closed stream.
The control is re-read *between reads*, so a stop does not wait out the
outstanding batch. An **unanswerable** stop check stops — same fail-closed rule
as the control itself.

### If nothing is eligible

The historical 6.35% acceptance rate over 30,590 real BBO bodies is **not a
forecast**. 40 candidates at that rate is an expectation of ~2.5 markets and
could legitimately be **zero**. If selection returns no eligible markets within
the 40-candidate budget, that outcome is **reported and the probe stops**. The
budget will not be expanded and the frozen selection rule
(`MIN_SPREAD_TICKS=2`, `MAX_PRICE=0.50`, `MIN_SHARES_TRADED=100.0`) will not be
relaxed.

---

## 7. Rollback

Three steps, cheapest first.

1. **`render-ops sql obs-stop confirm=DO`** — the ordinary stop. Takes effect
   inside the process already running, within 30 s. **No deploy.** This is the
   control this stage exists to prove.
2. **`render-ops env-set BETTOR_LIVE_LOOP=off confirm=DO`** — the pre-check.
   ⚠️ Measured on 21 September: Render raised **no deploy** for this, and a
   `server_restarted` API call **did not reload the environment** (set
   22:38:58Z, restarted 22:43:40Z, still running discovery 22:47:29Z). A
   **deploy** did read it (00:31:22.805Z). So this step must be paired with an
   explicit `render-ops deploy confirm=DO`, and it is minutes rather than
   seconds. Not the first resort.
3. **Deregistration** — push
   `claude/bettor-observation-deregister-88d429e` @ **`be8f11639e05b5a0f4430bcf1805d9810cea3d40`**
   to `claude/session-njaewf`. **0 behind / 1 ahead** of `88d429ed`; `691fa598`
   is an ancestor, so it fast-forwards whether or not Stage 2 has deployed.
   Removes the registration line and its import — the supervisor cannot start
   what it is not handed. 24 other loops untouched.

`ca68bc5f` is **retired** and must not be used: it was 1 ahead / 5 behind and
not directly pushable. Every rollback here is prepared against the tip it rolls
back.

---

## 8. What this does not do

- **No order capability.** `MAX_CONTRACTS=0`; no submit/cancel/close call is
  reachable from the loop's import closure, asserted over the **source** rather
  than over behaviour.
- **No trading control changed.** `live_trading_paused`, `mirror_live`,
  `MAX_CONTRACTS` and the execution gate are read and left as they are. The
  gate is still awaited before any loop starts.
- **No funded pilot**, no capital, no new orders — a separate authorization.
- **No credential movement.** Availability is verified on the target service
  without exposing, moving, printing, hashing or logging any secret value.
- **No accounting record altered.** PREPROD/PRODUCTION and
  RETAIL/INSTITUTIONAL stay separated.
- **RN1 frozen.** Track P preserved as a negative result, not retuned.
  `edge-engine/src/edge/venues/pmus_stream.py` not modified.

---

## 9. Verification behind this request

- **232 tests pass** — `test_bettor_live_control.py` (44),
  `test_bettor_universe_probe.py` (51), `test_bettor_live_loop.py` (92),
  `test_bettor_market_stream.py` (45), 5.6 s.
- **Real PostgreSQL**, full budget lifecycle: absent → `BUDGET_UNREADABLE`;
  armed → `OPEN`, 40 remaining, 1800 s; consume 37 → 3 remaining; consume 3 →
  `BUDGET_EXHAUSTED`; deadline passed → `DEADLINE_PASSED` → `disarm()` writes
  `false` and only `false`; unreadable → closed. **Twenty concurrent
  single-market decrements all land** (`consumed == 20`), so the SQL-side
  increment loses no update.
- **`bettor_startup_path.py` exit 0**, against verbatim endpoint-shaped
  payloads: 400 candidates → 240 enriched in **240 reads** (no market read
  twice — the 480/400 wrap is fixed and a test reproduces it both ways) → 240
  considered → 22 selected → 22 subscribed → 22 durable records → **0 orders**.
- **`bettor_durability_proof.py` exit 0** — real server, real SIGKILL, two
  processes sharing only the database.
- **Marginal diff `691fa598..88d429ed`**: 11 files, +1,415 / −113.
- `render-ops.yml` 434,312 bytes — 77,688 under the 512,000 ceiling; YAML valid.

### Still not established, and deliberately so

- The **WebSocket message format and replacement semantics.** Paired HTTP
  `/book` bodies are useful fixtures; they are not evidence about the wire.
  No captured PMUS frame exists in the repository. Objective 2 is the first
  thing that could settle this.
- The **rate limit** for this endpoint and credential context (§4).
- The **database stop on production.** Stage 1 demonstrated the *environment*
  stop. §5 is the first evidence for the database one.

---

**Requested: approval for 2A and 2B as one package, at `88d429ed`.**
Awaiting that, nothing is deployed, the environment stop stands, and `obs-run`
is not set.

---

# STAGE 2A OUTCOME — 2026-09-22

**PASSED on all three required items. `obs-run` NOT set; the budget was never
armed** — see `STAGE2_BUDGET_GAP.md` for why 2B was stopped before arming.

## Pre-deployment

| Check | Result |
|---|---|
| Destination SHA | `691fa598` (unchanged since Stage 1) |
| Ancestry | `691fa598` **is** an ancestor of `88d429ed`; 0 behind / 6 ahead |
| `obs-state` | **`false`** at `01:23:40Z` (run 35675582029) |
| Push | `691fa59..88d429e` — **fast-forward, not forced** |

Environment applied **before** the deployment that loads it, five values at
`01:21:00Z`–`01:23:19Z`, all HTTP-acknowledged:
`BETTOR_PROBE_MAX_RPS=0.25`, `BETTOR_PROBE_CONCURRENCY=1`,
`BETTOR_FRAME_CAPTURE_N=25`, `BETTOR_LIVE_MAX_CONTRACTS=0`,
`BETTOR_LIVE_DISCOVERY_PAGES=6`, then `BETTOR_LIVE_LOOP=on` last.

## Both services on the approved SHA

| Service | SHA | Live at |
|---|---|---|
| sportsassets-workers | `88d429e` | `2026-09-22T01:24:52.140Z` |
| sportsassets-api | `88d429e` | `2026-09-22T01:24:56.332Z` |

## 1. The database control was read as false — FIRST PRODUCTION PROOF

```
01:25:10.176Z  INFO __main__: starting loop: bettor_live
01:25:10.181Z  INFO bettor_live_loop: not observing
               (STOPPED_BY_CONTROL: bare boolean false); effective config {...}
01:25:10.181Z  INFO bettor_live_loop: not starting (STOPPED_BY_CONTROL);
               holding 300s before the supervisor's own restart delay
```

`STOPPED_BY_CONTROL: bare boolean false` — **not** `disabled by
BETTOR_LIVE_LOOP=off`. The environment stop was cleared and the loop was held
by the DATABASE ROW. That is the gap the Stage 1 acceptance identified, now
closed on production.

Second refusal at `01:30:15.185Z` — **305.004 s** after the first, exactly the
declared 300 s hold plus the supervisor's 5 s delay.

## 2. No observation-related venue requests

Control read and refusal are **5 ms** apart, before any client is constructed.
Observation tables at `01:26:27Z`: `journal 0 / cursor 0 / ledger 0`, newest
`NULL` — unchanged against the 23:40:25Z baseline. Zero listing pages, zero
BBO reads, zero sockets.

## 3. The effective configuration matches — all eight verified

Render truncates log lines at ~400 characters, so the delivered line is cut at
`"kill_env":`. Values past that point were verified by **exact-substring log
filter** — a filter that matches proves the literal text is in the emitted
line:

| Filter matched | Verifies |
|---|---|
| visible in line | `max_rps 0.25`, `concurrency 1`, `frame_capture_n 25`, `max_contracts "0"`, `above_demonstrated_envelope false`, `probe_batch 120`, `rounds_at_start 2` |
| `budget_max_distinct_default": 40` | **40 distinct** |
| `budget_deadline_s_default": 1800.0, "budget_authority"` | **1800 s deadline**, and the no-false-claim marker |
| `"suspend_above_s": 120.0, "discovery_pages": "6", "listing_max_retries": 2, "listing_timeout_s": 20.0, "probe_max_retries": 3` | **Retry-After suspension**, listing bounds, probe retries |

Nothing missing, nothing incorrect, nothing unverifiable.

## State at the end of 2A

| | |
|---|---|
| `obs-state` | **`false`** |
| `obs-budget` | **ROW ABSENT** — "a probe without a declared budget does not run" |
| `live_trading_paused` | **`true`** — unchanged |
| Observation tables | 0 / 0 / 0 |
| Orders | 0 |

Both guards are independent and both closed: with no `bettor_live_probe_state`
row, `read_budget` returns `BUDGET_UNREADABLE` and the loop refuses **even if
`obs-run` were set**.
