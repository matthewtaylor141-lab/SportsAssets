# Probe 2 — a bounded transport-and-observation experiment

**Two independent outcomes.** Transport verification and unchanged
strategy evaluation. The probe is complete when the transport question
is answered, **whether the frozen rule admits zero markets or many**.

No trading. No relaxed economic thresholds. No capital activation. The
worker holds no order path — `grep` for `orders.`, `place_order` or
`create_order` in `backend/sportsassets/workers/bettor_live_loop.py`
returns nothing, and that is asserted by a test rather than claimed
here.

---

## 0. What probe 3c413436 could not answer, and why

It returned INCOMPLETE. The reason was structural, not operational:

```
the frozen rule admitted 0 of 40 markets
  -> nothing was subscribed
    -> the WebSocket received no frame
      -> the transport stayed unverified
        -> and would have stayed unverified for as long as
           the strategy kept saying no
```

A transport question was made to depend on an economic answer. The two
are independent and are now run independently.

Three further defects that run exposed, all repaired below:

| # | finding | status |
|---|---|---|
| 1 | subscription gated on strategy admission | repaired — §1 |
| 2 | the 40 markets were the 40 **alphabetically first** slugs, one family | repaired — §2 |
| 3 | **one** reserved listing unit covered **up to six** outbound requests, unpaced | repaired — §3 |

---

## 1. Observation is not trading admission

`bettor_live_loop.observation_subset()`.

A small, bounded, deterministic subset of **valid, open** markets is
subscribed for observation **even when the frozen rule rejects their
spread or liquidity**.

**What is filtered, and what is not.**

| excluded from observation | reason |
|---|---|
| `NOT_OPEN` | the venue does not call it open; it cannot produce a meaningful frame. A **transport** fact |
| `PRICE_UNPARSABLE` | the book will not parse; same |
| a price field present but unreadable | a body we could not read. Reported separately as `excluded_malformed_body`, because `assess` files it under `ONE_SIDED_BOOK` |

| **kept** in observation despite refusal | the rule's verdict is **recorded, not waived** |
|---|---|
| `SPREAD_BELOW_MIN_TICKS` | recorded on every journal row |
| `ASK_ABOVE_MAX_PRICE` | recorded |
| `TRADED_VOLUME_BELOW_MIN` | recorded |
| `NO_TRADED_VOLUME_FIGURE` | recorded |
| `ONE_SIDED_BOOK` | recorded; ranked last, because a two-sided book produces more transport evidence per frame |

**The rule itself is untouched.** `MIN_SPREAD_TICKS = 2`,
`MAX_PRICE = 0.50`, `MIN_SHARES_TRADED = 100.0`,
`UNIVERSE_VERSION = BETTOR_UNIVERSE_V1`. The tests assert against those
constants, not against copies of their values.

**Deterministic, and fixed before the economics are seen.** The order
is the seeded candidate permutation fixed at listing time (§2). There
is one pass over what was already enriched. Nothing re-reads,
re-searches or re-draws until a market passes.

**Durable, per record.** Every journal row a watched market produces
carries:

```json
{"observation_basis": "OBSERVATION_ONLY",
 "universe_rule_reason": "SPREAD_BELOW_MIN_TICKS",
 "universe": "BETTOR_UNIVERSE_V1"}
```

so a refusal is readable from the journal forever, not from a startup
log line the next restart overwrites.

**Bound — a TOTAL subscription cap.** `WATCH_MAX = 8` bounds the
**union** of strategy-admitted and observation-only markets, not the
observation-only additions alone.

An earlier draft let admitted markets be watched *in addition* to the
eight. That made the declared limit describe neither number: one
admitted plus eight observed is nine subscriptions under an "eight
market" authorization. The union is now capped, admitted markets take
priority within it in the rule's own volume-ranked order, and any
truncation of the rule's choices is reported by name in
`strategy_admitted_dropped_by_cap` — a run that watched fewer markets
than the rule chose is a different run and must not be silent about it.

**A malformed body is never subscribed.** `assess()` reports
`ONE_SIDED_BOOK` both for a side that is *absent* (a real market state,
worth observing) and for a side that is *present and unreadable*
(`{"value": "not-a-number"}`). Those are the same reason string and
different kinds of fact. `assess` is frozen and is not changed;
`observation_subset` asks the narrower question separately via
`_prices_parse()` and excludes bodies it could not read, listing them
in `excluded_malformed_body`. Subscribing on an unreadable body would
mean subscribing on nothing.

---

## 2. Reproducible sampling — and what it does not establish

`bettor_universe_probe.order_candidates()`.

`candidates_from_listing` sorts by slug, and the enrichment window
walks that order from an offset. Probe 3c413436's 40 markets were
therefore the 40 alphabetically first slugs, and came back as one
family: `aachc-mlb-bavg-*-leader-*`.

The candidate list is now permuted by a **keyed BLAKE2b digest of the
slug** — `SAMPLE_ORDER_VERSION = BLAKE2B_KEYED_SLUG_ORDER_V1`. Not
`random.shuffle`, which is reproducible only for one PRNG
implementation; a keyed digest can be regenerated by a reviewer from
the seed and the listing alone.

**The seed for this probe is declared here, before the run:**

```
BETTOR_PROBE_SAMPLE_SEED = PROBE2-2026-09-22
```

Recorded in the run: seed, order version, candidate population,
population family count, sampled count, coverage of the listing,
sampled family count and the largest sampled family's share.

**What this establishes:** coverage **within the bounded listing**.

**What it does not establish:** representativeness of the venue, of any
market family, or of any period other than the one observed. The
listing is itself a page-bounded prefix — 6 pages × 500 — and a
permutation of a prefix is still a prefix.

`family_of()` is a **coarse naming heuristic**, not the venue's
taxonomy: `eventSlug` where present, otherwise the first three
dash-separated tokens of the slug. It is reported so a reader can see
whether a sample came from one corner of the listing. No selection
decision is made from it.

---

## 3. The listing budget, reconciled

### 3a. Six pages against one reservation — confirmed

`_list_candidates` took **one** reservation and then ran

```python
while pages < max_pages:            # max_pages = 6
    resp = client_.markets.list({...})
```

Two consequences, both present in probe 3c413436's own numbers:

* `listing_attempts_reserved: 1` was written while the venue received
  up to **six** requests — an undercount of up to **6×**.
* Those requests went through **no pacer at all**. The run opened with
  a six-request burst and was nevertheless reported as running at
  0.25 req/s. **The rate the venue saw at the start of that probe was
  never 0.25 req/s.** That materially weakens the earlier 429 analysis,
  which was built on a configured number treated as an observation.

### 3b. Does pagination or retry happen inside an SDK call? — verified, no

Read from the installed `polymarket_us` package, not assumed:

| check | finding |
|---|---|
| `Markets.list` | `return self._client.get("/v1/markets", query=...)` — a thin wrapper, **no loop, no pagination** |
| `PolymarketUS.__init__` | `httpx.Client(timeout=timeout)` — no `transport=`, no `retries=`; httpx defaults to `retries=0`. **No internal retry** |
| `follow_redirects` | appears **nowhere** in the package → httpx default `False` → a 3xx is raised as `APIStatusError`. **No hidden redirect request** |
| `_request` | no `sleep`; a 429 raises `RateLimitError` immediately. **No internal backoff loop** |

**Therefore one wrapper call is exactly one outbound HTTP request**, and
reserving per call is reserving per request. If a future SDK version
changes any of this the equality breaks silently, so it is asserted
against the installed package by
`TestTheSDKAddsNoHiddenRequests`.

### 3c. The repair

* **One reserved unit per page**, taken before the request and before
  the pacer — an unfunded page is never issued.
* **A retry costs its own unit.**
* **Every listing request is paced**, through the *same* pacer the
  enrichment rounds use, so the process has one outbound rate instead
  of one per call site.
* `Retry-After` is honoured **verbatim** and never shortened; above
  `PROBE_SUSPEND_ABOVE_S = 120s` the listing is abandoned rather than
  retried sooner.
* **Repeated throttling stops the listing** immediately at
  `LISTING_MAX_CONSECUTIVE_429 = 2` — not after spending the rest of
  the page's retries on the same answer.
* A later page failing keeps the rows already paid for and flags
  `listing_incomplete`; only page 1 failing is a failed listing.

`listing_attempts` and `listing_pages_requested` are now the same
number by construction. **A divergence between them and the budget
row's `listing_attempts_reserved` is a defect**, and that is the check
to run after the probe.

### 3d. Concurrent-request telemetry

`Pacer.telemetry()` now records, per process:

```
requests_started, observed_span_s, observed_mean_rps,
densest_1s_starts, densest_10s_starts, max_concurrent_inflight,
paced_wait_s, penalties, penalties_s
```

and every 429 records what the process was doing **at that instant**:
`observed_starts_last_1s / _10s / _60s`, `observed_inflight`,
`at_s_into_pacing`, `retry_after_s`.

**This narrows the question; it does not answer it.** Every figure is
scoped to *this process*. Other workers on the same service, anything
else holding the same credential, a shared egress address and
endpoint-specific ceilings are all invisible from here. Each recorded
event carries `attribution: NOT ESTABLISHED` for that reason.

### 3e. The proposed rate — a conservative experiment, not a safe limit

```
BETTOR_PROBE_MAX_RPS = 0.10        (one request per 10 seconds)
BETTOR_PROBE_CONCURRENCY = 1
```

Roughly **2.5× slower** than the configured rate of the run that was
throttled, and — unlike that run — applied to **every** outbound
request including the listing.

This is **not an identified safe limit.** The applicable limit remains
`NOT_ESTABLISHED`. Nothing about 0.10 req/s has been shown to be
tolerated; it is a conservative setting chosen to reduce the chance of
contributing to throttling while the telemetry above collects the
evidence that would let a rate be reasoned about at all.

---

## 4. What the run must demonstrate

| # | question | how it is answered |
|---|---|---|
| 1 | a real PMUS WebSocket connection | `stream.stats()`: `first_connected_at`, `epoch ≥ 1`, `connected` |
| 2 | frames actually received | `updates > 0`; up to **25 captured frames** verbatim, **total received reported separately** |
| 3 | timestamps and payloads parse correctly | journal rows carrying `source_ts`, `received_at`, `source_age_s`, `receipt_age_s`, `venue_state`, `stats_shares_traded` — and the refusal histogram if they do not |
| 4 | durable observations **including strategy refusals** | `bettor_live_journal` rows with `observation_basis` and `universe_rule_reason` |
| 5 | **obs-stop issued while work is active** | §5 — issued on an *observed* condition, not a guess |
| 6 | verified cessation of requests | budget counters static across ≥ 58 s |
| 7 | verified stream closure | the **stop receipt**, §4a |

### 4a. The stop receipt — closure measured, not inferred

"The stream closed within 90 seconds" was previously argued from the
**absence** of new journal rows. A quiet market produces exactly the
same absence.

**A thread exiting is not proof that a socket closed.** A thread can
end with its close having raised, or having never run, or having never
connected at all. Three events are now stamped separately and only one
of them means the connection shut:

| stamp | meaning |
|---|---|
| `socket_closed_at` | `ws.close()` **returned**. The only closure evidence |
| `thread_exited_at` | the socket thread ended. Not closure |
| `stop_requested_at` | when the worker asked the socket to close |

`stop(wait_s=)` joins the thread and returns one verdict:

| verdict | `closed` | when |
|---|---|---|
| `CLOSED` | true | thread gone **and** `close()` returned cleanly |
| `INCOMPLETE_JOIN_TIMEOUT` | false | the wait expired, thread still running — **a failure, not a slow success** |
| `INCOMPLETE_CLOSE_RAISED` | false | `close()` raised; the error is named |
| `INCOMPLETE_THREAD_EXITED_WITHOUT_CLOSE` | false | thread ended, `close()` never returned |
| `NEVER_STARTED` | false | no thread, so no connection to close |

`close_latency_s` is `None` when there is nothing to subtract.
**UNMEASURED is None, never zero.** A non-`CLOSED` verdict is logged at
ERROR and `shutdown_verified: false` goes on the receipt.

The worker writes `ingestion_state.bettor_live_stop_receipt`:

```
IDENTITY      probe_id, boot_id, pid, host, receipt_written_at
THE FOUR TIMES
  control_detected_at      when THIS PROCESS read the stop
  stop_requested_at        when it asked the socket to close
  socket_closed_at         when ws.close() RETURNED
  last_request_started_at  the last outbound HTTP request it STARTED
  (+ last_frame_at, thread_exited_at)
VERDICT       transport.shutdown, shutdown_verified, close_latency_s
ACQUISITION   reserved counters, process-pacer telemetry, 429 events
              stream, frames, watching, durability, orders_submitted: 0
```

The operator's own issue time is not visible from inside the process,
so the gap between it and `control_detected_at` is the control-poll
interval (`CONTROL_EVERY_S = 30 s`) and belongs to the operator's
record, not to this one. Reporting one as the other would flatter the
measurement by up to 30 seconds.

Read with `render-ops sql obs-stop-receipt` (read-only). A receipt that
cannot be written is a warning, never a change to how the loop shuts
down — the shutdown has already happened by then.

### 4b. The request budget is GLOBAL, and here is the worst case

Listing, enrichment, every refresh and every retry share **one durable
counter set** (the `bettor_live_probe_state` row) and **one pacer**
(built once in `main()` and passed to every discovery).

An earlier build created a pacer per `_discover` call, so each refresh
got a fresh pacer whose `_next_at` started at zero and its first
request ignored the rate entirely. That is fixed; the configured rate
now describes the **process**.

**Worst case under the authorized budget, at 0.10 req/s:**

| counter | cap | seconds of pacing |
|---|---|---|
| `max_listing_attempts` | 18 | 180 |
| `max_bbo_attempts` | 160 | 1,600 |
| **total outbound requests** | **178** | **1,780** |

The absolute deadline is **1,800 s**, so the request budget and the
deadline bind at very nearly the same point — neither can be exceeded
by the other running long. Both are durable and survive a restart:
the counters do not reset and the deadline is an absolute timestamp.

**Expected case:** ~6 listing + 40 BBO = **46 requests ≈ 460 s**, after
which the distinct-market allowance (40) is exhausted and any later
refresh is refused at the reservation before a request is issued.

## 5. Stopping while the stream is live

> *"Waking later and refusing does not substitute for measuring stop
> latency during active work."*

On the last run, obs-stop was issued while the worker sat in an
`EMPTY_UNIVERSE` backoff. It refused, correctly, and measured nothing.

### 5a. The trigger is the SOCKET, not the HTTP counters

An earlier draft required `bbo_attempts_reserved` to be **rising** at
the moment of the stop, and manufactured a discovery refresh (by
shrinking `BETTOR_PROBE_BATCH`) to make such a window exist. Both were
wrong:

* it spends requests for no reason other than to create a convenient
  test window, and
* it makes the stop test **impossible** in the ordinary case where the
  acquisition allowance is already exhausted — which is exactly what a
  40-market budget does within the first eight minutes.

The transport question does not need an HTTP request in flight. The
trigger is:

```
issue obs-stop when the socket is confirmed open AND fresh evidence
has arrived:

  journal_rows_last_60s  > 0        durable observations, this minute
  journal_age_s          < 60       the newest row is fresh
```

A committed journal row means a frame arrived, parsed, and was
decided — so the socket is open and delivering. That is the condition,
read from the database, from the worker's own durable output.

`BETTOR_PROBE_BATCH` is left at its default. No request is issued to
manufacture a window.

### 5b. Acquisition cessation is measured SEPARATELY, and only if it applies

Whether an HTTP request happens to be active at stop time is an
accident of timing, not a precondition. It is reported either way:

| at stop time | what is measured |
|---|---|
| `last_request_started_at` within ~120 s | acquisition was live; assert the budget counters are unchanged at T+58 s |
| otherwise | acquisition had already ceased; recorded as **NOT EXERCISED THIS RUN**, not as a pass |

The receipt carries `last_request_started_at` from the **process
pacer**, so the claim rests on the worker's own record of its last
outbound request rather than on a counter read from outside.

### 5c. The sequence

```
t≈0      arm, run
t≈0-60   listing: up to 6 paced requests, one reserved unit each
t≈60-460 enrichment: 40 paced BBO reads (the distinct allowance)
t≈460    stream.start()  -- frames begin
t≈460+   poll obs-live every 20 s
         >>> journal_rows_last_60s > 0  ->  ISSUE obs-stop <<<
T+58 s   obs-budget: counters unchanged (if acquisition was live)
T+90 s   obs-live:   journal no longer growing
then     obs-stop-receipt: shutdown == CLOSED, close_latency_s
t≈1800   deadline disarms if nothing else has
```

If no journal row ever appears, the stop is issued at T+900 s
regardless and the run is reported as **transport NOT VERIFIED**, with
the refusal histogram as the finding. That is a complete result, not a
failure to complete.

## 6. The exact activation request

**No broadening of the previous authorization.** Every numeric limit is
the one already approved. The listing allowance of 18 is unchanged —
what changed is that the worker now actually spends it one unit per
request instead of one unit per six.

### Commit

```
branch (untracked, no auto-deploy)   claude/bettor-none-pool-fix
SHA to deploy                        <FROZEN AT PUSH -- §7>
base                                 e8f616a  (current production, all services)
```

### Deploy-time preflight (all read-only, all before arming)

```
1  git ancestry: the SHA descends from e8f616a
2  render-ops deploys        both services' running SHA == e8f616a
3  render-ops sql obs-state  == false                 (not observing)
4  render-ops sql pause-state                          (trading controls unchanged)
5  render-ops sql obs-budget                           (prior probe record read and preserved)
6  render-ops sql obs-schema                           (deployed schema, not an assumption)
7  push the exact SHA, no force
8  render-ops deploys        both services now running the new SHA
9  render-ops logs           effective config FROM THE WORKER ITSELF
```

Step 9 must show, from the worker's own log line:

```
listing_reservation_unit         ONE_PER_OUTBOUND_REQUEST
listing_paced                    true
listing_max_outbound_requests    18
watch_max                        8
observe_max                      8
observe_is_not_trading_admission true
sample_seed                      PROBE2-2026-09-22
max_rps                          0.1
concurrency                      1
max_contracts                    0
```

### Environment (on the worker service only)

```
BETTOR_PROBE_MAX_RPS        = 0.10
BETTOR_PROBE_CONCURRENCY    = 1
BETTOR_LIVE_WATCH_MAX       = 8      TOTAL subscriptions (the union)
BETTOR_LIVE_OBSERVE_MAX     = 8      observation-only additions
BETTOR_PROBE_SAMPLE_SEED    = PROBE2-2026-09-22
BETTOR_LIVE_MAX_CONTRACTS   = 0
BETTOR_LIVE_DISCOVERY_PAGES = 6
BETTOR_LIVE_DISCOVERY_PAGE_SIZE = 500
BETTOR_FRAME_CAPTURE_N      = 25

BETTOR_PROBE_BATCH is LEFT AT ITS DEFAULT. An earlier draft shrank it
to manufacture a discovery-refresh window for the stop test; that
spends requests for no reason but the test, and §5a removes the need.
```

### Budget — `obs-arm`, once

```
max_distinct          40      distinct markets
max_bbo_attempts     160      including retries
max_listing_attempts  18      including retries -- now one unit per OUTBOUND REQUEST
deadline            1800 s    absolute
probe_id            fresh
```

### Sequence

The previously reviewed **`render-ops` operator sequence**.
`.github/workflows/bettor-probe.yml` remains **NOT FOR USE**: its three
claimed safety properties were checked and none held
(`WORKFLOW_DEFECTS.md`). Arming stays a human-authorized dispatch, so
"one authorization cannot arm twice" is enforced by the authorization
itself.

```
sql obs-budget            read and PRESERVE the prior probe record
sql obs-state             confirm stopped
sql pause-state           confirm trading controls unchanged
sql obs-stop-receipt      read the prior receipt, if any
deploys / logs            confirm running SHA and effective config
sql obs-arm      DO       arm once
sql obs-budget            read back before running
sql obs-run      DO       start
sql obs-live              poll every 20 s -- the §5 trigger
sql obs-stop     DO       issue on the observed condition
sql obs-budget            T+58 s: counters unchanged
sql obs-live              T+90 s: journal no longer growing
sql obs-stop-receipt      closure, measured
```

**No replenishment. No deadline extension. No automatic second probe.**

### Explicitly not authorized by this request

* no orders, and no order path exists to place one;
* no funded trading, no change to any trading flag;
* no credential movement, no credential printing, no partial reveal;
* no change to legacy accounting records;
* no relaxation of any strategy threshold;
* no continuous collection — the deadline disarms the control.

---

## 7. Release hygiene

| branch | tracked by a service | auto-deploy |
|---|---|---|
| `claude/session-njaewf` | all services | **yes** |
| `claude/bettor-none-pool-fix` | none | no |

Preparation and documentation commits go to
`claude/bettor-none-pool-fix`. The reconciliation at the start of this
work:

```
claude/session-njaewf   == e8f616a == both services' running SHA   YES
beyond deployed         50e717a, 2887e82  (documentation only)
git diff e8f616a 2887e82 -- backend/      EMPTY
```

The probe SHA is frozen at the moment of the push to the tracked
branch and recorded in §6 then — not before.

---

## 8. What this probe still cannot establish

* **Fill-conditioned maker EV.** It rests no order, so it observes no
  fill. `P(FILL | STATE, QUOTE)` and
  `E[SETTLEMENT − QUOTE | FILLED, STATE]` remain NOT_IDENTIFIED.
  Removing state-selection bias leaves fill-selection bias exactly
  where it was.
* **`E[SETTLEMENT − QUOTE | STATE]`.** Observation supplies the QUOTE
  and STATE sides only. The estimand additionally needs matched
  authoritative settlements, adequate coverage, and a valid clustered
  evaluation — none of which is a by-product of streaming. The probe
  moves this from *unstartable* to *started*.
* **The venue's rate limit.** The telemetry narrows the question and
  records what this process was doing; attribution across a shared
  credential or egress address is not visible from here.
* **Representativeness.** Coverage within a page-bounded listing is not
  coverage of the venue.
* **Working capital.** `$41,667` is a scenario under a two-hour holding
  assumption and a stated turnover convention, with two of three
  capital components set to zero because they are unmeasured. It is not
  an unconditional lower bound.
