# RUN 83 PREREGISTRATION — AMENDMENT V2

```
PREREGISTRATION_VERSION = RUN83_PREREG_V2
AMENDED_AT              = 2026-09-12
SUPERSEDES              = RUN83_PREREG_V1
STATUS                  = FROZEN BEFORE COLLECTION
FIRST_VALID_COHORT      = none yet collected under this version
```

Frozen before any second activation. Nothing below may change once a cohort has
begun collecting under it; a further change is V3 and says so.

---

## 1. WHAT DID NOT CHANGE

The scientific question is unchanged:

> After BETTOR first receives an RN1 source event, how does executable price
> evolve?

The pre-registered offsets are unchanged:

```
0 / 100 / 250 / 500 ms / 1 / 2 / 5 / 10 / 30 / 60 s
```

**The short windows were NOT widened**, and the tolerance rule is unchanged
(`window_for(t) = max(50 ms, 0.20 × t)`). Widening them to make an HTTP request
fit would erase the interval the experiment exists to measure. Primary sizing
remains `Q_A = 0.10 × size`. `DEPTH_EXHAUSTED` remains
`NOT_IDENTIFIABLE_FROM_RETAINED_DEPTH` and is never extrapolated.

## 2. WHY THE MECHANISM CHANGED

`RUN83_ACTIVATION_FAILED_V1` (activation 2026-09-12T15:56:25Z, disabled
16:06Z) proved the V1 mechanism cannot observe the pre-registered short
horizons. V1 issued **one new HTTP request per (event × offset)**. Measured:

| | |
|---|---|
| captured | 337 of 125,270 scheduled (**0.27%**) |
| captured at 0 / 100 / 250 / 500 ms / 1 s / 2 s | **0, at every one of the six** |
| captured at 60 s | 315 of 337 |
| scheduler lag | ~72 s, stable |
| actual venue reads | 0.476/s against a 2.0/s ceiling — **the pacer was idle** |

The binding constraint was not the read rate. It was
`collector.py::run`'s `asyncio.Semaphore(max_inflight())`, which a task holds
**for its whole 60-second horizon**, capping the collector at `8 / 60 s =
0.133 events/s` against an RN1-only requirement of 0.187–0.255 events/s. The
architecture fails even at the correct population.

Two facts make a request-per-slot mechanism unfixable for the short end rather
than merely under-provisioned:

1. **Arithmetic.** At RN1's measured p99 burst (10 fills in one second) the
   `0ms` slot alone requires 10 reads inside its 50 ms window — 200 requests/s
   instantaneously; at the measured maximum (43 fills in one second), 860/s. No
   sustained pacing ceiling reaches that, at any RPS.
2. **Causality.** A request *initiated after* receipt cannot observe
   receipt-time state. Measured request duration was mean 182 ms, p50 170 ms,
   p95 227 ms. The `0ms` and `100ms` points are **inside the network round
   trip**. They were never observable this way.

The failed cohort is disqualified (`RESEARCH_ECONOMICS_ELIGIBLE = FALSE`,
`INSTRUMENT_CAPACITY_FAILURE`), so changing the instrument before the first
valid cohort costs no evidence.

## 3. THE PRIMARY CHANNEL — AND AN HONEST NAME FOR IT

**The primary channel samples state BETTOR already holds locally.** At each
scheduled monotonic offset the collector records what is in its own
market-state cache at that instant. The scheduled operation performs **no
network I/O**. This removes both failures above: the sample cannot be inside a
round trip, and a burst costs no requests.

### 3.1 The channel is NOT a stream, and is not named as one

Owner Decision 2 named this channel `FAST_STREAM_PATH`. **It is implemented
here as `LOCAL_CACHE_BATCH_POLL_PATH`, and the rename is deliberate.**

Established, not assumed (`py-clob-client` 0.34.6, fetched and read
2026-09-12):

- **The vendor SDK has no websocket client at all** — zero files matching
  `websocket|wss|ws_`. There is no vendor-supported market-data stream to
  subscribe to.
- `GET /book` (one token) and **`POST /books` (a list of tokens)** are the
  book endpoints. The batch form is what makes a maintained cache affordable.
- `OrderBookSummary` carries `market`, `asset_id`, **`timestamp`**, **`hash`**,
  `bids`, `asks`, `min_order_size`, `neg_risk`, `tick_size`,
  `last_trade_price`.
- **There is no sequence number.** `hash` is a content fingerprint: it proves
  *change*, and cannot prove *ordering* or *absence of a gap*.

So the cache is fed by batched HTTP polling, not by a pushed feed. Labelling a
polled cache `FAST_STREAM_PATH` would be the same class of error as labelling
the legacy HTTP read `FASTEST_AVAILABLE_BOOK_PATH` — which this project
explicitly refused. `FAST_STREAM_PATH` stays **declared and unimplemented**,
reserved for a genuine pushed feed if one is ever found.

`STREAM_CONTINUITY_UNVERIFIED` is therefore the **standing** state for every
sample, not an exception: with no sequence number, continuity cannot be proved.
The cache is not treated as incrementally maintained. Each batch response
**replaces** that token's state wholesale (the endpoint returns a full ladder),
so there is no delta stream to lose and no bootstrap to miss — but equally, no
basis to claim the local book stayed complete between refreshes.

### 3.2 The resolution limit, stated up front

A polled cache's honesty depends entirely on one measured number:

```
cache_age_ms = sample_monotonic - state_received_monotonic
```

**The instrument's real short-horizon resolution is bounded by the refresh
interval, and no scheduling change improves it.** If the hot-token refresh
interval is R, then offsets closer together than R sample the *same cached
state* with different ages. That is not a measurement of price movement between
them — and reporting it as one would be the "flat curve by fiat" defect that
`workers/price_path.py` was rewritten to remove.

This amendment therefore does **not** claim the 0–500 ms horizon is now
resolved. It claims something narrower and true: **every sample carries its own
age and its own state identity, so the analysis can tell whether BETTOR's
knowledge actually changed between two offsets instead of assuming it did.**
Whether the achieved R is small enough to answer the primary question at the
short end is an empirical result of the first valid cohort, not a design claim.

Two pre-registered quantities exist to measure exactly that, and they must be
reported before any price-evolution figure:

- `distinct_state_fraction(offset)` — the fraction of events whose sample at
  that offset carried a **different** `venue_book_hash` from the `0ms` sample.
  At offsets where this is ~0, the instrument did not observe change; it
  observed the same state twice.
- `cache_age_ms` distribution per offset.

## 4. THE 0 MS DEFINITION

`0ms` does **not** mean "a reading taken at receipt". No mechanism can do that.

```
0ms  :=  the latest valid market state already present in BETTOR's local
         market-data cache at FIRST_BETTOR_RECEIPT_MONOTONIC.
```

and its age is persisted:

```
receipt_cache_age_ms = FIRST_BETTOR_RECEIPT_MONOTONIC
                       - state_received_monotonic
```

This is the amendment's most important single quantity. It separates two
possibilities that V1 could not distinguish and that the mandate depends on:

- BETTOR **already knew** the degraded price before the RN1 event arrived
  (`receipt_cache_age_ms` small, and the state at 0 ms already carries the
  deterioration) — the edge was gone before we heard about it; or
- the book **changes after** receipt (the state at 0 ms is undegraded and later
  offsets differ) — the edge existed and we lost it in our own latency.

A large `receipt_cache_age_ms` means neither conclusion is available for that
event, and it is excluded from the primary endpoint rather than averaged in.

## 5. THE POPULATION

```
PRIMARY POPULATION = RN1-confirmed  AND  canonical was_insert = true
```

- **Subject**: `RN1_OBSERVABILITY_SUBJECT_WHALE_ID`, no default. Unset,
  malformed or unknown ⇒ `OBSERVABILITY_SUBJECT_NOT_CONFIGURED` and **nothing
  is collected**. The admission key is the immutable whale id, never the
  username; username and wallet address are retained as provenance only.
- **First-receipt only**: admission requires `was_insert = true` from the
  canonical `INSERT INTO trades … ON CONFLICT (dedupe_key) DO UPDATE …
  RETURNING (xmax = 0) AS was_insert`. The research instrument adds no second
  dedupe authority.

V1 had neither filter. Its cohort was 94.671% other wallets (20 on the
ingestion roster, RN1 5.329%) and 94.6% fills the ledger had already held — up
to 36.9 days old — re-presented by the poll lane's page walk.

## 6. THE ANCHOR

```
PRIMARY ANCHOR = FIRST_BETTOR_RECEIPT_MONOTONIC
```

stamped on entry to `ingest_trade_result`, **before** the canonical insert.
Admission happens after the insert, so admission is strictly later than the
anchor; the delay is recorded separately and never folded into it:

```
admission_monotonic
admission_delay_ms = admission_monotonic - FIRST_BETTOR_RECEIPT_MONOTONIC
```

The database insert delay must never redefine receipt time. An event whose
`admission_delay_ms` already exceeds an offset's window has that offset
recorded `MISSED_ADMISSION_LATE` — a named, counted miss, never a silent one
and never back-dated.

## 7. PRIMARY ENDPOINTS

Reported on the primary channel, from `Q_A`, with `cache_age_ms` and
`sample_lateness_ms` attached to every point:

1. best executable ask (and the executable-side equivalent for a sell)
2. `Q_A` executable VWAP where retained depth is valid, else
   `NOT_IDENTIFIABLE_FROM_RETAINED_DEPTH`
3. deterioration from RN1's own source price
4. deterioration from the state observed at immediate receipt (the `0ms` state)
5. the fraction of the 1 s / 2 s / 5 s deterioration **already present at
   receipt**
6. `distinct_state_fraction(offset)` and the `cache_age_ms` distribution
   (§3.2) — reported first, because 3–5 are uninterpretable without them

## 8. THE SECONDARY CHANNEL

`LEGACY_COMPARABLE_BOOK_PATH` is retained as a **secondary diagnostic channel
only**, for continuity with U2 and runs 81A/81B. It keeps its own request
start, response complete, lateness and failure records.

Three rules, enforced in schema and code:

- The validity of the 0–500 ms primary curve **does not depend** on REST
  capacity.
- A delayed HTTP response is **never** substituted for a missing primary
  sample. Different channel, different row, different `observation_slot_id`.
- The channels are never pooled into one average. `observation_channel` is on
  every row and is part of the slot identity.

## 9. WHAT IS NOT PERMITTED

No threshold tuning from interim data. No economics analysis before the first
cohort is sealed. No inspection of outcomes to change this analysis. The failed
cohort's 337 captures are **not** used to estimate latency decay: capture was
selected by scheduler survival and window length (315 of 337 at 60 s), and
94.6% of the cohort was not live flow, so those anchors are not what they claim.

---

`RUN83_PREREG_V2` is frozen as of this commit. The first valid cohort will
carry `preregistration_version = 'RUN83_PREREG_V2'` on every plan row, so a row
can never be read against a version it was not collected under.
