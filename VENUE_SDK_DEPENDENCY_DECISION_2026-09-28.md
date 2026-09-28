# The venue SDK dependency: decided, pinned, and measured

Date: 2026-09-28. Branch: `claude/command-center`. This closes the fifth of
Codex's five repair points — *"Finish the dependency decision as engineering…
Do not defer an ordinary dependency decision to me."*

No credential, control, deployment, protected worker or order was changed. The
protected worker (`bettor_live_loop`, pinned `f5d1c05`) builds its own client
through `bettor_universe_probe.Pacer` and is untouched by every change here.

---

## 1 · What the deployed image was actually running, and how that was established

`backend/Dockerfile` builds with `RUN pip install --no-cache-dir .` against
`polymarket-us>=0.1.2`. An unbounded floor plus a build-time resolution means
**the deployed retry behaviour was decided by whatever PyPI had latest at the
minute the image was built**, and nothing in the repository or the running
process recorded which one that was.

Published versions, read from PyPI:

| Version | First uploaded |
|---|---|
| 0.1.1 | 2026-01-22 |
| 0.1.2 | 2026-01-22 |
| 1.0.0 | 2026-09-22 |
| 1.0.1 | 2026-09-23 |
| **1.0.2** | **2026-09-24** |

The serving build `c3d0cfc` was committed 2026-09-28 12:30 UTC, after 1.0.2
was published, so `pip install .` resolved **1.0.2**. That agrees with the
independent reproduction, which reported `polymarket-us 1.0.2`,
`max_retries: 2`, 3 HTTP attempts for 1 paced call.

**This container had 0.1.2 installed.** That is the substantive finding: the
local test suite was exercising a client with *no retry logic at all* while
production ran one that retried twice. The 429 amplification was not
reproducible locally, and could not have been — not because the tests were
weak, but because they were testing a different dependency.

The environment has been upgraded to 1.0.2, so every measurement below is
against the build production resolves.

## 2 · The interface, checked rather than assumed across a major version bump

Every resource method this repository calls exists on 1.0.2 with the
positional-`params` shape the call sites already use:

`markets.{bbo,book,list,retrieve_by_slug,settlement}` ·
`orders.{cancel,close_position,create,list,preview,retrieve}` ·
`portfolio.{activities,positions}` · `events.list` · `account.balances` ·
`search.query`

`PolymarketUS` and `AsyncPolymarketUS` take the same keywords
(`key_id, secret_key, gateway_base_url, api_base_url, timeout, max_retries`),
so the synchronous and asynchronous constructors agree. `self._http =
httpx.Client(timeout=timeout)` is present, which is the attribute both
`venue_http_observer` and the request gate reach for.

## 3 · The retry behaviour, read out of the installed wheel

| Property | 1.0.2 |
|---|---|
| Default `max_retries` | 2 → **3 attempts per logical call** |
| Retryable statuses | 408, 409, 429, 500, 502, 503, 504 |
| Retryable methods | GET, HEAD, OPTIONS, DELETE |
| **POST retried** | **Never** — the API has no idempotency key, so a retry after a partial failure could submit a duplicate order |
| Where it retries | inside `_request`, with its own `time.sleep(backoff_delay(...))` |
| `Retry-After` parse | **integer seconds only**; an HTTP-date returns `None` |
| Backoff | 0.5 s × 2^attempt, capped 8 s, equal jitter |

Two of those matter beyond bookkeeping:

* The retries happen **inside one logical call**, spending a decision deadline
  the SDK knows nothing about. That is why they had to be turned off rather
  than merely counted.
* The SDK **drops a date-form `Retry-After`**. Ours parses both forms, and
  ours is the one that arms the cooldown — so the venue naming its window in
  date form no longer becomes "the venue named no window".

## 4 · The decision

**Pin `polymarket-us==1.0.2` and construct with `max_retries=0`.**

1.0.2 rather than 0.1.2 because 1.0.2 is what the deployed image already
resolved: pinning the older one would be a silent downgrade of production
dressed up as reproducibility.

`max_retries=0` because retrying is still correct — it just has to be the
retry that knows when to stop. Ours is bounded at
`pmus.BOOK_READ_MAX_DISPATCHES = 2`, counted per logical read, waits through
the gate on the venue's own window, and re-checks the decision deadline before
every dispatch. Two retry mechanisms stacked multiply the request count for
the same answer.

The decision lives in two places and the suite asserts they agree:

* `backend/pyproject.toml` — what an image installs.
* `backend/sportsassets/venue_sdk.py` — what the running process constructs
  against and **reports**. `venue_sdk.report()` rides the heartbeat, so
  "which SDK is deployed" is now answered by asking the process rather than
  by reading a build log an operator does not have.

`venue_sdk.report()["sdk_retries_disabled"]` is conditioned on the constructor
having actually **accepted** the keyword. It is never True merely because the
keyword was offered.

## 5 · The test that defended the defect, removed

`test_the_pyproject_range_is_recorded_as_unbounded` asserted that
`pyproject.toml` still read `polymarket-us>=0.1.2`. It was written to stop the
problem being rediscovered and had the effect of protecting it: **fixing the
range would have failed the suite.** A test may record an open question; it
must not make closing the question a failure.

It is replaced by four that assert the decision instead: the pin holds in both
files and they agree; the installed build is the pinned one *and* its retries
are off; the pinned build never retries POST; and a date-form `Retry-After`
survives our parse even though the SDK drops it.

## 6 · Measured behaviour through the actual scheduled path

`backend/tests/test_the_scheduled_read_is_rate_controlled_end_to_end.py` — 17
tests, all passing — drives the real chain and replaces only the socket:

```
_read_book_blocking → pmus.book_read → polymarket_us 1.0.2
  → httpx.Client → PacedTransport (ours) → MockTransport
```

| Case | Established |
|---|---|
| success | 1 logical read → **1** request (was 3) |
| 429 on every attempt | exactly **2** dispatches, the declared budget |
| 429 then success | book returned, and the second dispatch is **≥ the venue's `Retry-After`** later, measured on the transport's own clock |
| transport failure, no response | counted as an attempt (this is the case that reported `attempts: null`), classified transient, retried |
| 404 | **not** retried, no cooldown armed — an absent market is not a busy venue |
| hold armed, generous deadline | no request dispatched **before the permitted instant** |
| hold 600 s, deadline 2 s | refused by name, **zero** dispatches |
| deadline already passed | refused, zero dispatches |
| no deadline at all | capped at `MAX_UNDEADLINED_WAIT_S`, refused rather than parked |
| two consecutive reads | **2 and 2** attempts (the per-path counter reported 3 then 6) |
| two overlapping reads, same slug, two threads | 1 attempt each, no shared counter |
| restart boundary | 429 → queue → drain → globals cleared → *a read at that moment is permitted, asserted* → startup read-back → the same read is now refused with zero dispatches |
| failed write | the cooldown stays queued for the next cycle |
| failed read | reported `read_failed`, never as "no cooldown" |

### The counterexamples were checked against their own absence

Re-enabling the SDK's retries (`client_kwargs()` returning `{}`) fails **6 of
the 17**, and the amplification is visible directly: one logical read made
**3 HTTP attempts against a declared budget of 2**. The tests gate the
decision rather than describing it.

## 7 · What the four earlier points now stand at

1. **The cooldown prevents requests.** `penalize_observed` arms a hard
   not-before instant at the transport, separately from the reduced-rate
   period. The two are named separately everywhere they are reported:
   `not_before` is a prohibition, `hold_s` is
   `REDUCED_RATE_PERIOD_NOT_A_PROHIBITION`. A hold outlasting the deadline
   refuses by name; nothing sleeps past its caller; the gate re-checks inside
   `handle_request`, with nothing between the check and the send.
2. **Attempts are per-read and complete.** Counted before dispatch (so a
   response-less request counts), responses counted separately, ids minted per
   logical read, process totals kept under
   `scope: PROCESS_SINCE_IMPORT, is_not_a_per_read_count: true`.
3. **The diagnostic reaches a reader.** `error_detail` is merged into the
   scheduled projection with measured fields winning over absent ones, and the
   end-to-end tests assert on `out["diagnostic"]["http_status"]` — the final
   consumer, not the producer.
4. **Restart persistence is implemented, not reported as unimplemented.**
   `venue_cooldown_store.load_and_resume` is called at loop startup after the
   writer lock and before the first cycle; `drain_pending` runs at the top of
   every cycle. A 429 on the read path queues rather than writes, because that
   path has no connection — and `queue_save` returns
   `is_durable_yet: False` rather than claiming otherwise.

## 8 · What this does not establish

* No funded activation is authorized by any of this, and funded submission
  stays disabled.
* The rate-limit **scope** — whether the venue counts against the credential,
  the account or the source IP — is still not established. The stored row is
  keyed by scope so a later correction makes old rows stop matching rather
  than being silently reinterpreted, but the scope itself remains
  `SCOPE_CREDENTIAL` by assumption. No cross-service causation is claimed.
* The restart test uses a connection double. A real PostgreSQL round trip
  belongs in the `funded-pg18` gate job and is not yet added there.
* These are component and integration tests in this container. They are not
  production verification, and release evidence still has to be on the exact
  deployed SHA.
