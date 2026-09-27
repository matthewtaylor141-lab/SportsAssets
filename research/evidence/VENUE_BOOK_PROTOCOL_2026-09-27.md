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

## 5 · `transactTime` — and the argument I used is WITHDRAWN

**This section previously asserted a determination. The determination is
withdrawn, and the withdrawal is recorded here rather than edited away.**

### What I argued

> "A response stamp cannot precede its own response. So `transactTime`
> materially before `date` **contradicts** the response-stamp reading — one
> observation suffices in that direction."

### Why that premise is false — two ways, either sufficient

1. **A timestamp can describe representation GENERATION**, which happens *before*
   transmission. A stamp earlier than the `Date` on the wire is exactly what a
   generation instant looks like. Nothing is contradicted.
2. **A cached representation retains its original timestamp.** These very
   responses carried `cf-cache-status: EXPIRED` then `HIT` under
   `cache-control: public, max-age=30`, and §4 shows `last-modified` holding
   still across the gap while `date` advanced — the mechanism in action.

**So the representation-generation hypothesis was never excluded.** I ruled out a
hypothesis the argument had no power to rule out. That is the same error as the
resampling inference I withdrew earlier, wearing a more rigorous-looking sleeve.

### The observations, which stand

```
read 1  date: 16:24:27   transactTime: 2026-02-20T03:07:30.947946180Z   levels 0/0
read 2  date: 16:24:35   transactTime: 2026-02-20T03:07:30.947946180Z   levels 0/0
        last-modified 16:24:27 on both      state: MARKET_STATE_EXPIRED
        date − transactTime = 18,969,408.2 s (219.6 days)
        across the 8 s gap: date +8.0 s, transactTime +0.0 s
```

### What they establish

**The value was OLD IN THE RETURNED REPRESENTATION.** That is all. It is a fact
about what came back, not about what the field denotes.

### The four hypotheses, all open, none preferred

| | |
|---|---|
| **H1** | last book change |
| **H2** | last trade |
| **H3** | settlement / market close |
| **H4** | **representation generation** — precedes transmission, survives caching unchanged |

On an **expired, zero-depth, cached** market H1–H4 *all* predict an old value.
These reads have **no discriminating power at all**.

### What would resolve it, and what would not

**WOULD:** the venue's contract for the field. Nothing on the pages read states it.

**WOULD NOT, on its own:** a moving-book experiment. Correlation between a moving
book and a moving stamp is *evidence* about H1 against H3/H4; it is **not a
semantic guarantee**, and it must not become the next asserted certificate after
transport latency, our receipt instant and an HTTP validator.

### What turns on it, without presuming the answer

Under **H1** an old value means a quiet money line, and a 30 s bound on it
refuses every quiet market while calling it freshness — the bound applied to the
wrong quantity, which is a **separate repair and not a loosening**. Under **H4**
it is a property of the representation and bounds the response, like `Date − Age`.
Under **H2/H3** it is neither. The repairs differ, so **the field is not gated on
and nothing depends on it.**

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

| mechanism / precondition | verdict | basis |
|---|---|---|
| **M1 P1** full-replacement authority | **ESTABLISHED** | the published subscription-types table |
| **M1 P2** liveness | **AVAILABLE** | a `Heartbeat` message type exists |
| **M1 P3** connection continuity | **AVAILABLE** | connection epochs, bridged from the production stream. A drop DISCARDS every market's state |
| **M1 P4** instrument identity | **AVAILABLE** | `marketSlug`, compared rather than assumed |
| **M1 P5** documented timing | **NOT ESTABLISHED** | zero published sentences. **This is the only unmet precondition** |
| **M1 P6** resynchronisation | **AVAILABLE** | corrected. See below |
| ~~M1 gap-free delta continuity~~ | **WITHDRAWN AS A REQUIREMENT** | no delta mechanism is documented; the requirement was the wrong shape for a snapshot feed |
| **M2** the conditional exchange | **AVAILABLE** | `Last-Modified` present, conditional GET → 304 |
| **M2** as a market-data clock | **CONTRADICTED** | `last-modified` today over a book that cannot have moved since February |
| **M3** `Date − Age` | **AVAILABLE, and a PARTIAL** | bounds the origin's response, never the book |
| **`transactTime`** | **UNRESOLVED** — four open hypotheses | §5 |

### P6 was corrected, and it was two errors, not one

I recorded resynchronisation as NOT ESTABLISHED because *"the client implements
none"*. The SDK does not reconnect — **our wrapper does.** `RN1XMarketStream._main`
reconnects with backoff, increments its epoch, and returns every known slug to
`REQUESTED` so it is resubscribed.

And on a **documented full-replacement feed** that *is* resynchronisation:
discard, reconnect, resubscribe, await the next authoritative replacement. There
is nothing to reconstruct, so no venue procedure is needed and none being
published is **not a missing guarantee**.

> **Missing SDK convenience is not an unavailable venue capability**, and I had
> reported it as the latter.

**A clean recovery is still not an age.** It establishes that we are not holding a
book across a drop. It says nothing about how current the replacement we then
receive is — and that was the fifth candidate that nearly became a freshness
certificate, after transport latency, our receipt instant and the 304.

### The single blocking item

**The venue publishes no timing guarantee for market data.** No as-of instant, no
latency bound, no staleness contract; zero matching sentences. Everything that
was ours to build is built; the one gap left is the one no engineering on our side
can fill.

**So four unmet preconditions became one — and the distance to a trade has not
shortened.** That is a clearer position, not a nearer one.

**And substituting an observable for it is the mistake to refuse.** Four
predicates were proposed, found unsupported and replaced; a fifth was queued. All
five are recorded in `backend/sportsassets/market_data_design.py` with why each
failed, so a sixth is recognisable.

**Until the venue documents it the lane's verdict stays `NOT_ESTABLISHED`, and
that is not a claim that any book is stale.** What has changed is that *"our
current M1 predicate cannot qualify this feed"* is the accurate statement, and
*"the venue has no engineering route"* — which I wrote — is not.
