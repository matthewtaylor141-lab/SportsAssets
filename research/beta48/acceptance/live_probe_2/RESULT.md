# Live observation probe 2 — result

**Verdict: ACCEPTED, with two limitations named below.**

Authorized: deploy exact SHA `ba870764f847e31b2704c5315ba53f7907b5c687`,
then run one bounded observation probe. Zero orders. No re-arming.

---

## 1. Identity — whose run this was

Every field below is read verbatim from the stop receipt the closing
process wrote (`ingestion_state.bettor_live_stop_receipt`), through
`render-ops sql obs-stop-verdict`, run 35739916192.

| | |
|---|---|
| probe_id | `f72d7bac-71e9-46f4-b717-df786554e5c6` |
| boot_id | `3ddd79c916e54190` |
| pid / host | `1` / `srv-d9gcv6urnols73ce6erg-5bb4489676-6sq6s` |
| loop | `BETTOR_LIVE_LOOP_V3` |
| running SHA | `ba870764f847e31b2704c5315ba53f7907b5c687` |
| seed | `PROBE2-2026-09-22` |

The receipt's `boot_id` equals the newest journal row's `boot_id`, and
its `probe_id` equals the armed `bettor_live_probe_state.probe_id`.
The receipt describes **this** probe, not a later supervisor restart.

## 2. Effective configuration — read from the running process

Not from a character count, not from an HTTP 200 on `env-set`. This is
the `effective config` line the worker itself logged at boot:

```
max_rps 0.1 · concurrency 1 · above_demonstrated_envelope false
probe_batch 120 · rounds_at_start 2 · frame_capture_n 25
max_contracts "0" · kill_env on · control_every_s 30.0
```

`max_rps 0.1` and `max_contracts 0` are the two that the authorization
turned on, and both are the running process's own report.

**This mattered.** `env-set` returned HTTP 200 and stored the value
without redeploying, and `restart` returned HTTP 200 without injecting
it. Only an explicit `deploy` applied it. Had the HTTP 200 been taken
as application, this probe would have run at **0.25 req/s — 2.5× the
approved rate**, under a single-use authorization.

## 3. Budget — every limit, authorized against actual

| | authorized | actual | |
|---|---|---|---|
| total subscribed markets | 8 | **8** (0 strategy-admitted, 8 observation-only) | within |
| distinct acquisitions | 40 | **40** | at cap |
| BBO attempts (incl. retries) | 160 | **52** | within |
| listing attempts (incl. retries) | 18 | **6** | within |
| shared rate | 0.10 req/s | **0.1003 observed mean** | at rate |
| in flight | 1 | **1 max** | at cap |
| captured frames | 25 | **14** | under |
| total frames received | report separately | **14** | — |
| absolute deadline | 1,800 s | ended at **~613 s**, never extended | within |
| orders | 0 | **0** | — |
| MAX_CONTRACTS | 0 | **"0"** | — |

### Reservation reconciles exactly to dispatch

```
reserved   {"listing": 6, "distinct": 40, "bbo_attempt": 52, "refused": 0}   = 58
dispatched  requests_started                                                 = 58
```

**58 = 58.** This is the check that `listing_attempts_reserved >= list_calls`
faked for six weeks: it passed while six outbound calls sat behind one
reservation, because `list_calls` counted the wrong boundary. The
listing repair is now confirmed live — `listing_attempts_reserved = 6`
for six dispatched pages, against the preserved prior probe record's
`1`.

Pacing: `observed_mean_rps 0.1003` over a 578.09 s span, `densest_1s 1`,
`densest_10s 1`, `max_concurrent_inflight 1`, `paced_wait_s 561.11`.
The pacer waited 561 of 578 seconds. Server backoff was honoured
verbatim — every 429 carried a `Retry-After` (1–8 s) that was slept
exactly, and each refusal recorded this process's own observed starts
with `attribution: NOT ESTABLISHED`.

## 4. Shutdown — the socket actually closed, on a live connection

| | |
|---|---|
| stopped_by | **`BUDGET_EXHAUSTED`** — the worker's own budget check, not an operator obs-stop |
| control_detected_at | 14:16:53.152026 |
| stop_requested_at | 14:16:53.152048 |
| socket_closed_at | **14:16:53.283909** (`ws.close()` returned) |
| thread_exited_at | 14:16:53.285212 |
| close latency | **0.132 s** |
| shutdown verdict | **`CLOSED`** |
| socket_close_error | none |
| socket_closes | 1 |
| thread_alive after join | **false** |
| **connected_at_stop** | **`true`** |
| connected_since | 14:16:23.297120 |
| epoch_at_stop | 1 · reconnects 0 · updates 14 |
| **active_close_exercised** | **`true`** |

**The connection was open when the stop was issued.** `connected_since`
14:16:23.297 with `epoch_at_stop 1` and `reconnects_at_stop 0` — one
connection, established 29.85 s earlier, carrying frames until
14:16:32.1. This is the fact that licenses the active-close claim, and
it is recorded *before* `self._stop = True`, not inferred from an
unchanged epoch afterwards. A clean `close()` on an already-dead socket
would have set `connected_at_stop false` and
`active_close_exercised false`; it did not.

`thread_alive: false` after a bounded 20 s join, and
`socket_close_returned: true` with no error. A timed-out join would
have reported `INCOMPLETE_JOIN_TIMEOUT` and `closed: false`.

Post-stop state: `bettor_live_observation = false`, written by the
worker's own `DISARMED` path. The supervisor has restarted the loop
fourteen times since, and each restart reads the control row and holds
without observing:

```
bettor_live_loop: not observing (STOPPED_BY_CONTROL: bare boolean false)
bettor_live_loop: not starting (STOPPED_BY_CONTROL); holding 30s
```

Trading controls unchanged; `orders_submitted: 0`.

## 5. What the probe produced

```
journal rows              14   (all OBSERVATION_ONLY, 0 STRATEGY_ADMITTED)
distinct markets seen      8
frames captured           14   of 25 wanted
frames received           14   — nothing dropped
distinct slugs in frames   8
records_written           14
```

Selection: 3,000 candidates listed over 6 pages, 40 sampled
(coverage 1.33%, 215 population families), 40 enriched, **0 eligible
for strategy** — every one refused `SPREAD_BELOW_MIN_TICKS`. The frozen
`BETTOR_UNIVERSE_V1` rule was applied `UNCHANGED`, and observation
proceeded anyway on the 8 observation-only markets. That separation is
the D1 repair working in production: *observation is not trading
admission*.

---

## 6. The two limitations, named

### (a) The stop was budget-initiated, not operator-initiated

The authorization said "once fresh observations are committed, issue
obs-stop." The worker's own budget check fired first, at 14:16:53,
30.088 s after the stream opened. I did not issue `obs-stop`, and I
have **not** re-armed to manufacture the operator-issued variant —
re-arming is explicitly outside this authorization.

What this does and does not establish: the **stop path** is fully
exercised, including the active close on a live connection, because
both paths converge on the same `stream.stop(wait_s=)` and the same
receipt. What is **not** exercised is the operator→control-row→worker
detection leg; `control_detected_at` and `stop_requested_at` differ by
22 µs here because the worker detected its own budget, not a row an
operator wrote.

### (b) Acquisition cessation was NOT EXERCISED THIS RUN

`last_request_started_at` is 14:16:22.894723 — **30.26 s before** the
stop. Acquisition had already finished when the stop fired, because
*budget exhaustion is what triggered the stop*. There was no in-flight
request to cease. Recording this as a pass would be recording the
absence of a thing as evidence that it was stopped.

### (c) The structural defect this run exposed

**The distinct-acquisition cap and the streaming window are coupled,
and the coupling collapses the observation window.**

```
14:06:44  loop starts, discovery begins
14:07:39  listing hits the 6-page bound (candidate set is a PREFIX)
14:16:22  last enrichment read -- 578 s of paced acquisition
14:16:23  stream opens, 8 markets subscribed
14:16:53  BUDGET_EXHAUSTED: 40 of 40 distinct slots reserved -> stop
```

A 1,800-second authorization produced **30 seconds of streaming**.
Enrichment consumes the entire distinct-market budget *before* the
stream opens, and the first budget check after subscription then stops
the loop. 14 frames, not 25, for exactly this reason — and the 1,786
seconds of unused deadline were never usable.

This is a real defect in the probe's shape, not in its execution. A
future observation window needs the distinct cap to bound *enrichment*
only, or needs the streaming phase to run under its own budget. **No
repair is made here** — that would change runtime code outside the
authorized SHA.

---

## 7. Answering the three questions asked separately

**1. Does the live observation infrastructure work?**

**Yes.** Listing → sampling → enrichment → selection → subscription →
frames → durable rows → bounded stop → verified socket close, all on
the authorized SHA, all inside every declared bound, with reservations
reconciling exactly to dispatch and the socket proven closed on a
connection proven live. The 30-second streaming window is a budget-shape
defect, not an infrastructure failure.

**2. Does the evidence support positive net EV for any policy in our
engine?**

**No — and this probe was never going to answer it.** 14 frames over 8
markets in 30 seconds is a transport receipt, not an economic sample.
The economic answer is in the consolidated package, and it does not
depend on this run.

**3. What execution measurement and capital would be needed to test a
surviving policy?**

An order path and capital, neither of which this authorization covers.
Specified in the consolidated economic package, not here.

---

## 8. Sources

| what | where |
|---|---|
| stop receipt, 45 fields | run 35739916192 (`obs-stop-verdict`) |
| live counters + control row | run 35739660400 (`obs-live`) |
| worker logs, boot → stop → held | run 35739679118 (`logs sportsassets-workers`) |
| prior probe record, preserved | `live_probe_3c413436/BUDGET_ROW_AT_PRESERVATION.json` |
| preflight, incl. the env-set finding | `PREFLIGHT.md` |
