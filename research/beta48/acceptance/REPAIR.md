# THE OBSERVATION REPAIR

| | |
|---|---|
| **prepared on** | `claude/bettor-observation-repair` |
| **based on** | production `255a7d38a1b3d72ddc33625370d8c0af2023b5b5` |
| **auto-deploys** | **NO** — verified, see §5 |
| current production | `claude/session-njaewf` @ `255a7d38`, loop DEREGISTERED |

`ACTIVATION.md` §12 is what the first live run did and why it was
stopped. **This is what was repaired, what it costs, and what is still
not proven.**

---

## 1. THE DEFECT, AND THE SOURCE THAT FIXES IT

`_discover` passed the venue's LISTING straight into the frozen
selection rule. `markets.list` returns `MarketDetail`:

```
id, slug, title, outcome, description, active, closed,
liquidity, volume, eventSlug, team
```

No `bestBid`. No `bestAsk`. No `sharesTraded`. No `state`. The rule
needs all four, so 3,000 of 3,000 markets were excluded as
`ONE_SIDED_BOOK` — a verdict about the market, for a fact about the
payload. **A missing field in a listing is not a one-sided market.**

### Both candidate sources were inspected, neither assumed

| | BBO `/v1/markets/{slug}/bbo` | websocket `MARKET_DATA` |
|---|---|---|
| captured PMUS evidence | **30,590 verbatim HTTP 200 bodies** | **none in this repo** |
| `sharesTraded` | present and **non-empty in 30,590 of 30,590** | declared on `stats`, never observed |
| `bestBid`/`bestAsk` | present as keys 30,590/30,590; non-null 23,914 | declared |
| `state` | present 30,590/30,590 | declared |
| replacement semantics | n/a (a snapshot read) | `ASSUMED_FULL_REPLACEMENT` |
| cost | **one call per market** | one subscription per 100 |

Corpus: `research/evidence/capture/run85_phase2_segment_*/request_log.jsonl.gz`.
The only recorded websocket frames in the repo are **global CLOB** — a
different venue, different field names.

**Enrichment reads the source that has been observed.** The websocket
is cheaper and its declared shape would serve, but a type declaration
is not a measurement, and this is the exact class of mistake that
produced the failure.

### Three findings that would each have broken a naive implementation

1. **The payload is wrapped.** Real bodies are `{"marketData": {...}}`.
   The SDK's `MarketBBO` declares the fields **flat** and omits
   `state`, which the live payload does carry. Reading the declared
   shape returns nothing at all.
2. **`sharesTraded` can be an empty string.** The 2026-09-05 CLOSED
   payload sends `""`. It is carried verbatim, becomes `None`, and the
   rule counts it `NO_TRADED_VOLUME_FIGURE` — which it already keeps
   apart from a market that traded nothing.
3. **`bidDepth`/`askDepth` are level COUNTS, not quantities** —
   `bidDepth == len(bids)` on 29,784 of 30,590 paired reads. Carried
   for sizing context, never as executable size.

`MarketDetail.volume` is **not** used. It is not `sharesTraded`, and
substituting it would silently change a frozen rule. A test greps the
probe's source for the word.

---

## 2. THE REPAIRED STARTUP EVIDENCE

`python3 scripts/bettor_startup_path.py` → **exit 0**

It drives `bettor_live_loop.main()` — the function `workers/all.py`
calls — with the endpoints' own shapes:

* `markets.list` → `MarketDetail` and nothing more;
* `markets.bbo` → `{"marketData": ...}`, **400 verbatim HTTP 200
  bodies**;
* the socket → the **paired verbatim `/book` bodies**, which already
  are the websocket payload shape.

Fixture: `bbo_book_real_400.json`. Only `marketSlug` was replaced; the
original is kept. Every price, quantity, ladder level, counter, state
and timestamp is exactly as the venue sent it.

| measured | |
|---|---|
| listing pages requested | 5 (a short final page ended pagination) |
| candidates identified | 400 |
| **enriched before the rule saw anything** | **400 of 400** |
| BBO reads issued | 480 |
| considered by the rule | 400 |
| **selected by the unchanged rule** | **34** |
| subscribed | 34, and `subscribed set == selected set` |
| confirmed by arriving data | 34; failed 0 |
| durable records written | 34, 0 persist failures |
| evidence class | `REPLAY_DECISION` (the transport's, not the loop's) |
| orders | **0** |

Exclusions, all economic: `SPREAD_BELOW_MIN_TICKS` 327,
`ASK_ABOVE_MAX_PRICE` 23, `ONE_SIDED_BOOK` 12, `TRADED_VOLUME_BELOW_MIN`
3, `NOT_OPEN` 1. **The 12 one-sided are the 12 whose `bestBid` the
venue really sent as `null`.**

Over the whole 30,590-body corpus the frozen rule admits **6.35%**.

### Coverage is reported apart from eligibility

`distinct_enriched` of `candidates` in `probed` reads, with probe
failures counted under their own names — `PROBE_READ_FAILED`,
`PROBE_NO_MARKET_DATA`, `PROBE_TIMED_OUT`, `NOT_PROBED_YET` — and never
as a rule reason. Section 3 of the runner empties the universe by
lowering one real counter below the floor and shows the difference:
`TRADED_VOLUME_BELOW_MIN` 37, `ONE_SIDED_BOOK` still 12, all 400 still
enriched.

### Two bugs the run itself found

* Four rounds of 240 over 400 candidates **wrapped and double-read**:
  `considered` came out 480 against a 400-candidate set, and duplicate
  rows reached `select` as separate entries — 46 "selected" collapsing
  to 34 subscriptions. Rows are now keyed by slug, freshest read
  winning, and a full sweep ends the round budget.
* The early-exit counted **enriched rows** instead of **eligible
  markets**, so it stopped after two rounds — about 30 markets at the
  measured admit rate — and called a third of a universe done.

---

## 3. THE REQUEST BUDGET

BBO is one call per market, so the cost is bounded by rotation: a round
reads `PROBE_BATCH` and advances a deterministic cursor over the
candidates sorted by slug.

| | |
|---|---|
| listing pages per discovery | 6 (limit 500) |
| BBO reads per round | 240 |
| rounds at startup (max) | 7 → **1,680 BBO reads** |
| concurrency ceiling | **8 in flight** |
| per-read timeout | 8 s |
| refresh interval | 900 s, **1 round** |
| reads per refresh | 240 BBO + 6 listing |
| **steady state** | **23,616 venue reads/day = 0.273 req/s** |
| rounds for a full sweep of 3,000 | 13 |
| time to first full sweep | 7 at startup + 6 refreshes = **1.5 h** |
| settlement reads | 3,600/day (batch 25 every 600 s) |
| control reads | 2,880/day — **the database, not the venue** |

Seven startup rounds comes from the admit rate: 1,680 × 6.35% ≈ **107
eligible**, so the 100-market cap binds. Four rounds would have yielded
about 61 and the cap would never have bound — a smaller universe than
the rule would have chosen, for no visible reason.

### What a refusal costs

A refusal is not free: `EMPTY_UNIVERSE` has already spent its discovery
before it returns. So the first hold is **300 s**, not 60:

| | reads | per | rate |
|---|---|---|---|
| retry at 60 s (rejected) | 1,686 | 81 s | 21 req/s |
| **retry at 300 s** | 1,686 | 321 s | **5.2 req/s** |
| second rung, 900 s | 1,686 | 921 s | 1.8 req/s |

Schedule **300 / 900 / 1800 / 3600 s**, reset the moment a run starts.
A `CONTROL_*` refusal costs **zero venue reads** — the control is read
first.

Before: `workers/all.py:run_forever` is a `while True` shared by
twenty-five workers, so a refusal returned in microseconds and retried
in five seconds. That loop is not this release's to change, so the
bound lives in `main()`.

---

## 4. THE STOP CONTROL, AND THE DEMONSTRATION

`bettor_live_observation` in `ingestion_state`, read **before anything
else** and again **every 30 s** while running, with a **10 s bounded
read**.

**ONE way to run:** an explicit `true`, or `{"run": true}`.
**FOUR ways to stop:**

| | |
|---|---|
| `false` | `STOPPED_BY_CONTROL` |
| no row | `CONTROL_ROW_ABSENT` — absence is not permission, per `execution_gate` |
| unparsable | `CONTROL_MALFORMED` |
| query raised or timed out | `CONTROL_UNREADABLE` |

A stopped loop costs nothing: **no listing page, no BBO read, no schema
initialization** — asserted in the runner and in the tests.

### Demonstrated on production, with no deployment

All three through `render-ops` dispatched from this branch. **No
service was deployed, restarted, or reconfigured.**

| when | action | result |
|---|---|---|
| 23:39:34Z | `sql obs-state` | `ROW ABSENT -- absence is not permission -- NOT OBSERVING` |
| 23:39:54Z | `sql obs-stop confirm=DO` | `INSERT 0 1`, row reads `false` |
| 23:40:25Z | `sql obs-rows` | journal **0**, cursor **0**, ledger **0** |

The third read also settles two claims from §12 of `ACTIVATION.md` by
measurement rather than by inference: **the three tables exist**, so
schema initialization did succeed, and **all three are empty**, so
nothing was ever observed or written.

### Stopping a RUNNING loop

That cannot be shown on production while the loop is deregistered, and
this document does not claim it. It is shown against the real
`main()`: `test_flipping_the_row_stops_a_running_loop_with_no_deploy`
starts the loop, changes one value, and the loop stops itself and its
stream within `CONTROL_EVERY_S` — no restart, no redeploy, no
environment change.

### Why not the environment variable

`BETTOR_LIVE_LOOP=off` was set at 22:38:58Z and acknowledged (HTTP 200,
3 characters stored). Render raised no deploy. A restart was requested
and **did** happen — `server_restarted` at 22:43:40.131647Z in the
service's own event log — and at 22:47:29Z the loop was still running
discovery. It is kept as a cheap pre-check and is **not** a
demonstrated control on this service.

**Deregistration remains the fallback.** `workers/all.py` still carries
the commented `LOOPS` line; re-registering is that one line.

---

## 5. THE BRANCH DOES NOT AUTO-DEPLOY

Read from the live service configuration, not assumed
(`render-ops action=services`, 23:39:06Z):

| service | branch | autoDeploy |
|---|---|---|
| `sportsassets-workers` | `claude/session-njaewf` | yes |
| `sportsassets-api` | `claude/session-njaewf` | yes |
| `edge-shadow` (suspended) | `claude/session-njaewf` | yes |
| `bettortoken-api` | `main` | yes |

`claude/bettor-observation-repair` is tracked by **nothing**. Pushing
to it cannot deploy. `render.yaml` pins no branch, so the per-service
setting above is authoritative.

---

## 6. WHAT CHANGED

| file | |
|---|---|
| `bettor_universe_probe.py` | NEW — listing → candidates, BBO → enriched rows, deterministic rotation, coverage |
| `bettor_live_control.py` | NEW — the database stop control, four ways closed |
| `workers/bettor_live_loop.py` | `_list_candidates` split out; `_discover` enriches before selecting; control read first and on a timer; every non-start path holds |
| `test_bettor_universe_probe.py` | NEW — 26 tests over **verbatim** BBO bodies |
| `test_bettor_live_control.py` | NEW — 29 tests, mostly refusals |
| `test_bettor_live_loop.py` | +13 — backoff schedule, control through `main()`, the live flip |
| `scripts/bettor_startup_path.py` | driven by endpoint shapes, not capture rows; §7 is the control |
| `scripts/bettor_market_measures_run.py` | same correction to its own fake client |
| `.github/workflows/render-ops.yml` | +8 lines: `obs-state` / `obs-run` / `obs-stop` / `obs-rows` |
| `bbo_book_real_400.json` | NEW — 400 verbatim `/bbo` + paired `/book` bodies |

Workflow file **433,216 bytes**, 78,784 under GitHub's 512,000 ceiling.

### Not changed

The selection rule and every parameter (`MIN_SPREAD_TICKS` 2,
`MAX_PRICE` 0.50, `MIN_SHARES_TRADED` 100, `MAX_MARKETS` 100,
`BETTOR_UNIVERSE_V1`); `execution_gate.py`; `live_trading_paused`;
`mirror_live`; `MAX_CONTRACTS`; `pmus.bbo_read`, which the frozen
mirror lane depends on. No credential moved. No order path reachable
from the loop's import closure. **The worker is still deregistered.**

---

## 7. VERIFICATION

| | |
|---|---|
| `test_bettor_live_loop.py` | **74 passed** |
| `test_bettor_live_control.py` | **29 passed** |
| `test_bettor_universe_probe.py` | **26 passed** |
| `test_bettor_live_store.py` | **59 passed**, real PostgreSQL |
| every bettor + universe suite | **1,348 passed** |
| `bettor_startup_path.py` | **exit 0** |
| `bettor_durability_proof.py` | **exit 0**, real PostgreSQL |
| `bettor_live_harness.py` | exit 0 |
| `bettor_market_measures_run.py` | exit 0 |

---

## 8. STILL NOT PROVEN

1. **That the socket connects and the venue accepts a subscription.**
   No captured PMUS websocket frame exists in this repo; the shape is
   SDK-declared and replacement semantics are formally
   `ASSUMED_FULL_REPLACEMENT`. Only the wire settles it.
2. **The venue's real update rate**, and therefore the real decision
   rate.
3. **The live admit rate.** 6.35% is measured over a 2026-09-13→20
   corpus of 12 distinct markets sampled repeatedly — not over a
   3,000-market cross-section on a given evening. The live figure could
   differ, which is why coverage is reported separately.
4. **That BBO is reachable from the worker's network path.** 30,590
   reads were taken by an unauthenticated public-gateway collector, not
   from this service.
5. Everything in `ACTIVATION.md` §11 that the funded pilot gates.

---

## 9. THE REQUEST

**No production action is requested by this document.** The repair sits
on a branch that cannot deploy, the worker stays deregistered, and the
control on production is `false`.

When activation is authorized, it is three steps and each is
separately reversible:

1. Fast-forward `claude/session-njaewf` to the activation SHA below —
   which deploys the code with the loop **still deregistered**, so
   nothing starts.
2. Uncomment the one `LOOPS` line and push — the loop registers and
   **immediately stops**, because the control reads `false`.
3. `render-ops sql obs-run confirm=DO` — one row, no deploy. Stop again
   with `obs-stop`.

| | |
|---|---|
| **activation SHA** | see below |
| branch | `claude/bettor-observation-repair` |
| destination when authorized | `claude/session-njaewf` |
| force | **never** |
