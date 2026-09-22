# The rate limit is real, and it was previously recorded as NOT ESTABLISHED

**This overturns a documented claim, on live evidence from the
authenticated credential context.**

## What the record said before this probe

> **Rate limit: NOT ESTABLISHED** for this endpoint and credential
> context. No `RateLimit-*` or `Retry-After` header in 65,980 responses;
> zero 429s; densest 1-second window exactly 1 request; observed range
> 0.030–0.285 req/s.
>
> The evidence is unauthenticated public-gateway traffic; the worker is
> authenticated.

That caveat turned out to be the whole story.

## What this probe measured

At a pace of **0.227 requests/second** — *inside* the previously
observed 0.030–0.285 envelope — the **authenticated** worker received
repeated HTTP 429s on `GET /v1/markets/{slug}/bbo`:

```
11:39:33.680  429 aachc-mlb-bavg-2026-09-29-leader-fertat   hold 10s  (attempt 1 of 3)
11:39:43.692  429 aachc-mlb-bavg-2026-09-29-leader-fertat   hold  0s  (attempt 2 of 3)
11:39:59.696  429 aachc-mlb-bavg-2026-09-29-leader-jacwil   hold  7s  (attempt 1 of 3)
11:40:30.719  429 aachc-mlb-bavg-2026-09-29-leader-luiarr   hold  9s  (attempt 1 of 3)
11:40:43.733  429 aachc-mlb-bavg-2026-09-29-leader-michar   hold 10s  (attempt 1 of 3)
11:41:05.780  429 aachc-mlb-bavg-2026-09-29-leader-ozzalb   hold 10s  (attempt 1 of 3)
11:41:47.813  429 aachc-mlb-bavg-2026-09-29-leader-yandia   hold  9s  (attempt 1 of 3)
```

Seven 429s across six distinct markets in 134 seconds.

## What this does and does not establish

**Does:**

- A rate limit **exists** on the authenticated BBO endpoint.
- **0.25 req/s is above it**, at least intermittently. The approved
  pacing was chosen from the unauthenticated corpus and is too fast.
- The venue supplies a **usable backoff hint**: the holds are 7–10 s and
  varied per response, so something server-side is being honoured rather
  than a fixed client constant. (The 0 s hold on attempt 2 is the client
  subtracting elapsed time from the hint, not the venue asking for 0.)
- The worker's handling is correct: it holds the **whole round**, not
  just the one slug, and retries are bounded at 3.

**Does not:**

- Give the limit's **numeric value**, its window, or whether it is
  per-endpoint, per-credential, per-IP or global. Seven observations
  over one 134-second window in one market family is not a
  characterisation.
- Say whether the limit is **fixed or adaptive**, or whether the MLB
  batting-average-leader family is treated differently from others.
- Establish what pace **would** be safe. A lower pace is indicated; the
  right number is not measured here.

`PMUS_AUTHENTICATED_BBO_RATE_LIMIT = EXISTS_VALUE_NOT_IDENTIFIED`

## Why it matters beyond this probe

Every capacity figure that assumed we could poll at 0.25 req/s per
worker needs the authenticated limit as an input, and it is now known to
be binding rather than absent. This does not change the economic verdict
— that turns on fill-conditional adverse selection, not on polling — but
it does change what a collector can observe per unit time, which is the
denominator of any freshness or coverage claim.
