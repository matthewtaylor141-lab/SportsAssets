# THE VENUE'S BOOK PROTOCOL — read directly, 2026-09-27

**Sources, both public, both read from the GitHub runner** (the build container's
egress denies these hosts, which is why these questions stood open in source):

| | |
|---|---|
| published WebSocket protocol | `https://docs.polymarket.us/api-reference/websocket/markets` — HTTP 200, 420,818 bytes |
| REST order book | `GET https://gateway.polymarket.us/v1/markets/{slug}/book` — HTTP 200 |
| runs | `command-verify` `book-protocol`, runs **36332797806** (16:19:25Z) and **36333087522** (16:24:19Z) |

No deployment was involved. I had earlier concluded that settling mechanism M2
required deploying our V3 probe, because that probe lives inside our API. **That
was a dependency I invented**: the endpoint is public, the runner has egress, and
a public response header does not need our backend to be readable.

---

## 1 · What I got wrong, first

> "The 'full order book' claim is a **DOCSTRING**."

**That was incomplete, and the venue's own page says so.** Its subscription-types
table reads, verbatim:

> `SUBSCRIPTION_TYPE_MARKET_DATA` — **Full order book and market stats**
> `SUBSCRIPTION_TYPE_MARKET_DATA_LITE` — Lightweight price data only
> `SUBSCRIPTION_TYPE_TRADE` — Real-time trade notifications

So full-replacement authority rests on the **published protocol**. The pinned
SDK's docstring agrees with it; it was never the only source, and reporting it as
the only source understated what the venue documents.

> "ETag is absent, so there is no engineering route."

**Wrong twice over.** A validator *is* present — `Last-Modified` — and the
conditional exchange works. And an absent validator would not have proved the
negative anyway.

## 2 · What the page documents, and what it does not

Sentence counts are from the retrieved page, searched for each concept:

| question | matching sentences |
|---|---|
| full book / snapshot / replace | **1** — the subscription table above |
| delta / incremental / diff / partial update | **0** |
| sequence / ordering / out of order / gap / missed message | **0** |
| heartbeat / reconnect / resubscribe / disconnect | **0** |
| latency / delay / "as of" | **0** (only the same table matched on `timestamp`) |

**This is a documented full-replacement feed with no incremental mechanism at
all.** So my P3 precondition — gap-free continuity, sequence numbers, delta
reconstruction — was **the wrong shape of requirement**. A subsequent
authoritative full replacement does not need earlier updates reconstructed:
there are no earlier updates to reconstruct, because there are no deltas.

What the page also does **not** document is **timing** or **reconnect**. That is
the real remaining gap in M1, and it is a different gap from the one I named.

## 3 · The REST book endpoint's response contract, verbatim

```
date:            Sun, 27 Sep 2026 16:24:27 GMT
last-modified:   Sun, 27 Sep 2026 16:24:27 GMT     <- equals `date`, to the second
expires:         Sun, 27 Sep 2026 16:24:57 GMT
cache-control:   public, max-age=30
cf-cache-status: EXPIRED        (then HIT on the conditional re-request)
age:             absent on the 200; `age: 0` on the 304
etag:            ABSENT
x-pm-server-latency: 0
x-pm-trace-id:   9273a5fa4b3cd1b0e0065eea3060000d
server:          cloudflare
cf-ray:          a41bdbd43fe52996-IAD
```

Payload: `{"marketData": {bids, marketSlug, offers, state, stats,
transactTime}}`. Probed for and **absent**: `seq`, `sequence`,
`sequenceNumber`, `msgId`, `messageId`, `isSnapshot`, `snapshot`, `action`,
`type`, `updateId`.

**The conditional GET returned `HTTP 304`.** So the M2 *exchange* exists.

### Three quantities, kept apart

| quantity | value | what it bounds |
|---|---|---|
| HTTP cache age (`Age`) | 0 on the 304, absent on the 200 | how long a cache has held this representation |
| origin generation (`Date − Age`) | ~0–1 s | when the **origin produced the response** |
| our observation age | ~0 s | when **we** received it |
| **upstream market-data age** | **not stated by any header** | when the **book** was what it says |

## 4 · The validator is present and is NOT a book clock — observed, not argued

On every market read, `last-modified` equalled `date` **to the second**, while
`transactTime` read **2026-02-20** — seven months earlier — with
`state: MARKET_STATE_EXPIRED` and **zero book levels**.

**The origin stamped the representation "now" over market data seven months
old.** That is precisely *"an origin can validate a representation while its own
market-data source is delayed"* — observed rather than hypothesised.

This is a **stronger** result than an absent ETag, and it points the opposite
way from the conclusion I drew. The validator exists; it is demonstrably not a
market-data age. So a 304 on this endpoint affirms the **representation**, and
**M2 must be recorded as CONTRADICTED for market-data currency**, with evidence,
rather than unavailable for want of a validator.

## 5 · `transactTime` — the discriminator, stated before it was run

Two readings have been open all day and they imply **opposite repairs**:

* **RESPONSE-STAMP** — an old value means our read is old, and the 30 s bound is
  measuring the right thing.
* **MARKET-DATA** — an old value means the **book has not moved**, and the bound
  is applied to the wrong quantity, which is a different repair.

**A response stamp cannot precede its own response.** So `transactTime`
materially before `date` contradicts the response-stamp reading — one
observation suffices in that direction, and no number of observations could
establish the positive direction. *That is why this is not the resampling I
withdrew:* nothing is being inferred from values that did not change.

**Result, on two independent markets:**

```
date − transactTime on read 1 = 18,969,408.2 s   (219.6 days)
  => RESPONSE-STAMP READING IS CONTRADICTED.
     A response stamp cannot precede its own response by 18,969,408 s.
     transactTime is a MARKET-DATA instant.

across the 8 s gap: date advanced 8.0 s, transactTime advanced 0.0 s
  => transactTime HELD STILL while date advanced.
```

`last-modified` also held still across the gap while `date` advanced — so
`last-modified` tracks the CDN's representation, not the response instant
either, and still not the book.

### What this establishes, and exactly what it does not

**ESTABLISHED:** `transactTime` is a **market-data instant**, not a response
stamp. This matters more than it may look: the dominant production refusal
`VENUE_QUOTE_STALE` compares `transactTime` against a 30 s bound, and the
question of whether that arithmetic measures anything at all turned on this.

**NOT ESTABLISHED:** *which* market-data instant. Last book change, last trade
and settlement all coincide on an expired, zero-depth book, so these
observations cannot separate them. Recorded as unresolved rather than resolved
in the convenient direction.

**NOT ESTABLISHED:** any numeric market-data age for a **live** book. All reads
landed on `MARKET_STATE_EXPIRED` markets with **0/0 levels** — the unfiltered
catalogue's first entries are settled 2025 NFL games, and the state filter added
in the second run did not move them, so the catalogue's list objects do not
carry state under the names tried. A zero-depth expired market cannot
characterise the currency of a live one.

## 6 · Runner versus production, recorded rather than assumed

This job read the gateway **directly from a GitHub runner**; production reads the
same host through `polymarket_us` from Render. Differences that could matter and
are **not** settled here:

* egress IP and region, so a different CDN edge may answer (`cf-ray … -IAD` here);
* the SDK sends `Authorization` on authenticated paths and this job sent none,
  so a validator could differ between them;
* Render's outbound path may add or strip hop headers.

**This settles the PROTOCOL, not our path.** Production integration still needs
its own gated deployment and its own readback.

---

## 7 · The supported market-data design, and its exact remaining assumptions

| mechanism | verdict | basis |
|---|---|---|
| **M1** live subscription — full-replacement authority | **ESTABLISHED** | the published subscription-types table |
| **M1** — gap-free delta continuity | **WITHDRAWN AS A REQUIREMENT** | no delta mechanism is documented; the requirement was the wrong shape |
| **M1** — instrument identity | **ESTABLISHED** | `marketSlug` on every payload, compared rather than assumed |
| **M1** — liveness | **AVAILABLE** | a `Heartbeat` message type exists in the pinned SDK |
| **M1** — documented timing | **NOT ESTABLISHED** | the page states no latency or as-of guarantee |
| **M1** — reconnect / resynchronisation | **NOT ESTABLISHED** | undocumented, and `base._message_loop` emits `close` and RETURNS: the client does not reconnect or resubscribe |
| **M2** conditional revalidation — the exchange | **AVAILABLE** | `Last-Modified` present, conditional GET → 304 |
| **M2** — as a market-data clock | **CONTRADICTED** | `last-modified` = `date` over data 219 days old |
| **M3** `Date − Age` | **AVAILABLE, and a PARTIAL** | bounds the origin's **response**, never the book |
| **`transactTime`** as the upstream market-data clock | **IS a market-data instant** / **denotation unresolved** | §5 |

**The one remaining dependency, named precisely.** Everything now turns on what
`transactTime` denotes on a **live** book. If it is the last book change, an old
value means a quiet money line and a 30 s bound on it **refuses every quiet
market while calling it freshness** — the bound would be applied to the wrong
quantity, which is a separate repair and not a loosening. If it lags a moving
book, it is a genuine delay and the bound is correct.

**That question cannot be answered by more observation of quiet books** — which
is the same trap as the inference I withdrew. It needs one of:

1. the venue documenting `transactTime`'s semantics (not on the pages read);
2. a read of a market whose book is **demonstrably moving**, where last-change
   and now diverge measurably. The catalogue read needs fixing first: the
   listing's state field was not found under `state` or `marketState`, so live
   markets have to be identified some other way.

**Until then the lane's verdict stays `NOT_ESTABLISHED`, and that is not a claim
that any book is stale.** What has changed is that
*"our current M1 predicate cannot qualify this feed"* is now the accurate
statement, and *"the venue has no engineering route"* — which I wrote — is not.
