# ACTIVATION REQUEST — OBSERVATION, STAGED

| | |
|---|---|
| **activation SHA** | **`691fa5985f461f381ebbbe90e17d458008ba5b25`** |
| branch | `claude/bettor-observation-repair` (**auto-deploys: NO**) |
| destination | `claude/session-njaewf` @ `255a7d38` |
| push | fast-forward, **never forced** |
| `obs-state` on production | **`false`**, read 2026-09-22T00:24:48Z |
| marginal diff | 14 files, **+3,248 / −116** |

`REPAIR.md` is the repair. **This is the request, its bounds, and its
rollback.** Nothing has been deployed and `obs-run` has not been set.

---

## 1. THE RATE LIMIT

### What the venue publishes: nothing

Measured over all **65,980** captured responses: the header set is
`date, content-type, cache-control, server, age` and nothing else. Not
one `RateLimit-*`, `Retry-After` or `X-Throttle` header. Not one HTTP
429. 61,178 answered 200; the other 4,800 are a **single segment that
is 100% 403** with a Cloudflare `server` header and an HTML body — a
WAF block of the whole host, not an endpoint throttle.

**So the applicable limit is UNKNOWN.** Absence of a published limit is
not absence of a limit.

### What is demonstrated

Every one of those reads was taken **strictly serially**. The densest
one-second window in the entire archive holds **exactly one request**,
at **0.030–0.285 req/s** sustained (median 0.145) across eight days.

That envelope is the default. Eight concurrent requests was never a
rate limit — it is a concurrency ceiling with no bound on the rate
behind it, and it was ~40× outside anything ever observed.

### What is enforced

| | |
|---|---|
| `PROBE_MAX_RPS` | **0.25 req/s** — the rate of the ROUND, not per task |
| `PROBE_CONCURRENCY` | **1** |
| mechanism | `Pacer` spaces request **starts**; both bounds applied |
| override | `BETTOR_PROBE_MAX_RPS`, `BETTOR_PROBE_CONCURRENCY` — reported, and flagged when above the demonstrated envelope |
| on 429 | `Retry-After` honoured, capped **120 s**; else **5/15/45 s**; **3 retries**; then `PROBE_RATE_LIMITED` |
| a 429 slows | the **whole round**, via `Pacer.penalise` |
| per-read timeout | 8 s (capture p99 = 181 ms; slowest single read 14.3 s) |

**Credentials caveat.** Those 30,590 reads were taken by an
**unauthenticated** collector against the public gateway. The worker
holds PMUS credentials; its limits may differ in either direction.
**UNVERIFIED.** Stage 3 is what settles it.

### No extrapolation

`PROBE_ROUNDS_AT_START` was 7, chosen so 1,680 × 6.35% ≈ 107 would
make the 100-market cap bind. **That is withdrawn.** The 6.35% corpus
is twelve distinct markets sampled repeatedly over eight days, not a
cross-section of the venue on any given evening, and a historical
acceptance rate is not a forecast.

Startup now reads a bounded **2 rounds** and begins with **whatever the
rule accepts**. The startup path reports 240 of 400 candidates covered
and **22** markets selected — partial coverage, stated beside the
verdict. **No 100-market universe is promised.**

---

## 2. THE COUNTS, RECONCILED

The previous report said *400 candidates enriched* and *480 reads*.
Both were true, and that was the problem: de-duplicating by slug fixed
the **counts** and left the **waste**.

**The extra 80 were the second round wrapping past the end of the
candidate list and re-reading the first 80 markets.** Two rounds of
240 over 400 candidates: 240 + 240, with 80 of the second overlapping
the first. The venue received 480 requests for 400 markets.

The window is now clamped to what the sweep still owes. A test
reproduces both states:

| | reads | distinct |
|---|---|---|
| without the clamp | 480 | 400 |
| **with the clamp** | **400** | **400** |

### Four numbers, reported apart

| | |
|---|---|
| `attempts` | HTTP requests issued, **retries included** |
| `retries` | of those, retries |
| `rate_limited` | of those, refused with 429 |
| `distinct_markets` | markets asked about |
| `enriched` | rows the rule can judge |

`attempts == distinct_markets + retries` is asserted, and
`distinct_enriched` is reported apart from `probed` so a wrapped round
can never again hide inside one number.

---

## 3. REPLAY EVIDENCE, SCOPED

**Withdrawn:** the claim that the paired `/book` bodies "already ARE
the websocket payload shape."

They are **HTTP responses**. They share a *declared* shape with
`_MarketDataPayload`, and sharing a declared shape is not evidence
about the wire. The replay does **not** establish:

* that real frames decode the same way;
* that `transactTime` arrives on the socket and parses;
* whether `bids`/`offers` **replace** the book or amend it.

**No PMUS websocket frame has ever been captured in this repo.** The
only recorded frames are global CLOB — a different venue.

### Stage-1 frame capture

`BETTOR_FRAME_CAPTURE_N` (off by default, ceiling 200) records, per
frame: keys present; keys **unexpected** and **missing** against the
declaration; whether an `asks` key ever appears; whether
`transactTime` is present and parses; ladder depth **frame over frame
per slug**; level and stats key names.

`frame_capture_report()` reads **`NOT_ESTABLISHED`** for decoding,
timestamps and book semantics until frames arrive. It reports
`DELTA_SUSPECTED` when a repeat frame loses half its depth, and it
does **not** promote `ASSUMED_FULL_REPLACEMENT` on its own — evidence
over a bounded sample is evidence, and a reviewer decides.

### Uncertain fields stay ineligible

Unchanged and reaffirmed: no `transactTime` → `NO_VENUE_SOURCE_TIMESTAMP`;
unparsable → the same; state not `MARKET_STATE_OPEN` → `MARKET_NOT_OPEN`;
a book from a previous epoch → `INVALIDATED_BY_DISCONNECT`. A missing
or unreadable field produces a **refusal that is recorded**, never a
guess.

---

## 4. THE STAGED SEQUENCE, WITH BOUNDS

### Stage 1 — deploy registered, control `false`

Fast-forward `claude/session-njaewf` to the activation SHA. Registration
does **not** start observation: `main()` reads the control first and
fails closed, so the loop wakes, reads one column, logs why it is not
observing, and returns.

**Acceptance — all four, or stop:**

| | bound |
|---|---|
| worker and API live on the activation SHA | both |
| `bettor_live: not observing (STOPPED_BY_CONTROL...)` in the log | present |
| **zero** observation venue requests | no `gateway.polymarket.us/v1/markets` line attributable to this loop |
| journal / cursor / ledger row counts | **unchanged at 0** |
| API `/healthz` | 200 |
| loop retry cadence | ≥ 300 s, not 5 s |

### Stage 2 — *(separately authorized)* `obs-run`, small bounded batch

`render-ops sql obs-run confirm=DO` — one row, no deploy. First
acquisition deliberately small:

```
BETTOR_PROBE_BATCH=40   BETTOR_FRAME_CAPTURE_N=25
BETTOR_PROBE_MAX_RPS=0.25   BETTOR_PROBE_CONCURRENCY=1
```

**40 BBO reads at 0.25 req/s ≈ 160 s**, 1 in flight, ≤ 6 listing pages.

**Verify:** actual stream frames captured and decoded; `transactTime`
present and parsing; book semantics reported; fresh decisions on both
clocks; rows committed to Postgres with zero duplicate record keys;
`attempts == distinct + retries`.

### Stage 3 — `obs-stop`, and the stop bound

**Declared bound on stopping:**

| | |
|---|---|
| new acquisition ceases within | `CONTROL_EVERY_S` (30 s) + `PROBE_STOP_CHECK_EVERY / max_rps` (20 s at 0.25 req/s) + one read timeout (8 s) = **≤ 58 s** |
| loop exits and stream closes within | the same 30 s control tick + `POLL_S` |
| **declared ceiling** | **90 s** from the `obs-stop` write to a closed stream |

Verified by: no further BBO reads in the log after the bound; the
`stopping on the control` line; `stopped_by = STOPPED_BY_CONTROL`;
journal row count static thereafter.

### Stage 4 — *only if stage 3 succeeds*

The 20-minute canary and the restart comparison, as in `ACTIVATION.md`
§8, with `scripts/bettor_canary.py` (exit 0 / 1 / **3 = incomplete**).

---

## 5. ROLLBACK

Three stops, cheapest first. **No force push, no history rewrite.**

| # | stop | needs a deploy? | effect |
|---|---|---|---|
| 1 | `render-ops sql obs-stop confirm=DO` | **no** | running loop stops within the 90 s ceiling; new starts fail closed |
| 2 | delete the row | **no** | absence is not permission — same result |
| 3 | re-comment the one `LOOPS` line and push | yes, fast-forward | loop deregistered; modules, tables and records all stay |

`claude/bettor-observation-deregister` @ `ca68bc5f` remains on the
remote as the prepared forward revert. Stop 3 is that one line.

**Not rolled back by any of these, because none of them touch it:** the
execution gate, `live_trading_paused`, `mirror_live`, `MAX_CONTRACTS`,
migration 093, the three tables, or any record already written.

---

## 6. WHAT IS FROZEN

| file | |
|---|---|
| `bettor_universe_probe.py` | NEW — listing→candidates, BBO enrichment, pacing, 429, rotation, coverage |
| `bettor_live_control.py` | NEW — the database stop control |
| `bettor_market_stream.py` | +125 — stage-1 frame capture, decode/timestamp/semantics report |
| `workers/bettor_live_loop.py` | enrich before selecting; control first and on a timer; bounded holds |
| `workers/all.py` | **+1 registration line** and its import |
| tests | +1,400 across probe, control, stream, loop |
| `bbo_book_real_400.json` | 400 verbatim `/bbo` + paired `/book` bodies |
| `render-ops.yml` | +8: `obs-state` / `obs-run` / `obs-stop` / `obs-rows` |

**Unchanged:** the selection rule and every parameter
(`MIN_SPREAD_TICKS` 2, `MAX_PRICE` 0.50, `MIN_SHARES_TRADED` 100,
`MAX_MARKETS` 100, `BETTOR_UNIVERSE_V1`); `execution_gate.py`;
`live_trading_paused`; `mirror_live`; `MAX_CONTRACTS`;
`pmus.bbo_read`. No credential moved. No order path reachable.

### Verification

| | |
|---|---|
| probe / control / stream / loop | **44 / 29 / 45 / 74** |
| every bettor + universe suite | **1,345** |
| store, real PostgreSQL | **59** |
| startup path · durability proof · harness · measures | **exit 0** ×4 |

---

## 7. STILL NOT PROVEN

1. That the socket connects and the venue accepts a subscription.
2. **The frame format, timestamps and replacement semantics** — stage 1
   captures frames to settle them; today they are `NOT_ESTABLISHED`.
3. The venue's real update rate, and so the real decision rate.
4. **The authenticated rate limit.** The evidence is unauthenticated.
5. The live admit rate. 6.35% is historical and is not a forecast.
6. That BBO is reachable from the worker's network path.
7. Everything in `ACTIVATION.md` §11 that the funded pilot gates.

---

## 8. THE REQUEST

**Authorize stage 1 only:** fast-forward `claude/session-njaewf` to
`691fa5985f461f381ebbbe90e17d458008ba5b25`, and confirm the worker
stays idle.

`obs-run` is a **separate** authorization. No funded pilot, no real
orders, no trading-control change.
