# VENUE MARKET-DATA TIMING — EVIDENCE RECORD AND SUPPORT REQUEST, 2026-09-29

**What this is.** The one precondition standing between the entry lane and an
established book currency is `P5_DOCUMENTED_TIMING`
(`backend/sportsassets/bettor_stream_currency.py`, `PRECONDITION_STATUS`,
`available: False`; `MISSING_PRECONDITIONS == ("P5_DOCUMENTED_TIMING",)`). P5 is a
property of the venue's **published** protocol: only the venue can supply it.
This file records exactly what the venue's public documentation says and does
not say about market-data timing as of 2026-09-29, and gives the owner a
ready-to-send request, through the venue's own published channel, for the
answers that would settle it.

**Documentation only.** No code path changes with this file. Nothing here is
evidence that P5 is met, and nothing here may be cited as such.

**No credentials, account identifiers or contact details** appear in this file,
and none should be added to the request before it is sent.

It sits beside `VENUE_BOOK_PROTOCOL_2026-09-27.md`, which recorded the REST
book's response headers and the four open hypotheses for `transactTime` (H1–H4,
§5 there). Nothing below supersedes that record; it adds the pages read on
2026-09-29 and the question that follows from both.

---

## (a) What the documentation says, and does not say — dated

Pages read on 2026-09-29 (UTC). URLs are the ones the docs' own index
(`https://docs.polymarket.us/llms.txt`, fetched 2026-09-29T21:49:55Z) lists.

| page | URL | read |
|---|---|---|
| Markets WebSocket (retail) | `https://docs.polymarket.us/api-reference/websocket/markets` | 2026-09-29 |
| WebSocket API Overview (retail) | `https://docs.polymarket.us/api-reference/websocket/overview` | 2026-09-29 |
| Get Market Book (REST) | `https://docs.polymarket.us/api-reference/markets/get-market-book` — endpoint `GET https://gateway.polymarket.us/v1/markets/{slug}/book` | 2026-09-29 |
| Changelog | `https://docs.polymarket.us/changelog` (through v0.0.93) | 2026-09-29T21:50:01Z |
| Streaming Best Practices (institutional gRPC) | `https://docs.polymarket.us/streaming-endpoints/streaming-best-practices` | 2026-09-29 |
| Streaming (institutional HTTP streams) | `https://docs.polymarket.us/trader-guide/streaming-apis.md` | 2026-09-29T22:32:49Z (fetch-docs run 36640194749, 6,338 bytes, sha256 `1de9da4fa49488a5c972be860b68f90e4f4c3bba56a76cc4869159646c108464`) |

### What they SAY, verbatim where quoted

* **Markets WebSocket:** "When debouncing is enabled, updates are batched and
  sent at regular intervals rather than on every change." The interval is not
  stated. `transactTime` appears **only inside an example payload**; no sentence
  states what it denotes.
* **WebSocket API Overview:** "Messages are delivered in sequence", with
  periodic heartbeats. The heartbeat interval is not stated.
* **Changelog (through v0.0.93, 2026-09-29):** no entry concerns market-data
  timing, `transactTime`, debouncing or delivery latency.
* **Streaming Best Practices (institutional gRPC):** describes `transact_time`
  **only for EXECUTIONS on Drop Copy** — "ordered per symbol, not across
  symbols", and compared with the wall clock to judge replay catch-up. It says
  nothing about the retail market-data book.
* **Streaming (institutional HTTP streams), read 22:32:49Z**, the sentences that
  bear on timing:
  * "Persistent connections: Streams use long-lived HTTP connections, not
    WebSockets (standard HTTP streaming), server pushes updates as they occur."
  * "Messages on a single stream are delivered in order."
  * "Messages across different streams may not be ordered relative to each
    other. If you need cross-stream ordering, use timestamps in messages,
    implement local ordering logic, and use sequence numbers where available."
  * "Some streams send periodic snapshots even if state hasn't changed to help
    detect missed messages, allow clients to reconcile state, typically every
    few minutes."
  * "Delivery is at-least-once."

  Its scope is the **institutional** HTTP/gRPC streams ("long-lived HTTP
  connections, not WebSockets") — **not** the retail Markets WebSocket or the
  REST book, which are what the entry lane reads.

### What they do NOT say — on any page read

1. **What `transactTime` denotes** on a Markets-WebSocket or REST book payload:
   the instant of the last book change, the instant the snapshot was generated,
   the instant it was sent, or something else. (Four hypotheses stay open:
   `VENUE_BOOK_PROTOCOL_2026-09-27.md` §5, H1–H4.)
2. **Any bound** on the delay between a change in the venue's book and its
   delivery — on the retail WebSocket, the REST book, or the institutional
   streams. "Server pushes updates as they occur" is qualitative and names no
   bound.
3. **Whether a non-debounced subscription delivers every book change**, and
   what the debounce interval is when it is on.
4. **Any as-of guarantee** for a REST `/v1/markets/{slug}/book` response: which
   instant the returned book was true at, or how old it may be. (The REST
   headers observed on 2026-09-27 — `last-modified` equal to `date`,
   `cache-control: public, max-age=30`, `Age` absent/0 — describe the HTTP
   representation, and `last-modified` was shown to move over a book that could
   not have changed: `VENUE_BOOK_PROTOCOL_2026-09-27.md` §4.)
5. The heartbeat interval on the retail WebSocket.

**Verdict for `P5_DOCUMENTED_TIMING` on 2026-09-29: NOT ESTABLISHED.** Zero
published sentences state an as-of instant, a latency bound, or a meaning for
any market-data timestamp on the feeds the lane uses.

---

## (b) The request — ready for the owner to send

**Channel.** The venue's own published channels, as linked from its
documentation footer: the Discord at `https://discord.gg/cA6Skf5wCk`; service
state is at `https://status.polymarketexchange.com`. (The status page reports
incidents; it is not a question channel.) Send from the owner's own account.
Do not paste any key, account id, order id or internal URL into the message.

> **Subject: Market-data timing on the Markets WebSocket and the REST book — four questions**
>
> Hello — we consume Polymarket US market data through the retail Markets
> WebSocket (`SUBSCRIPTION_TYPE_MARKET_DATA`) and the REST endpoint
> `GET /v1/markets/{slug}/book`. Before we treat a book as current we need to
> know what the venue guarantees about its timing, and we could not find it in
> the docs (we read the Markets WebSocket page, the WebSocket overview, Get
> Market Book, the changelog through v0.0.93, and the Streaming and Streaming
> Best Practices pages). Could you tell us:
>
> 1. **`transactTime`.** On a Markets-WebSocket market-data message, and on a
>    REST `/v1/markets/{slug}/book` response, what instant does
>    `marketData.transactTime` denote? Is it (a) the time of the last change to
>    that market's book, (b) the time the snapshot/representation was
>    generated, (c) the time the message was sent, or (d) something else (for
>    example the last trade or the market's close)?
> 2. **Every change?** With debouncing OFF, does a market-data subscription
>    deliver a message for every change to the book? With debouncing ON, what
>    is the batching interval, and can it be relied on as an upper bound?
> 3. **Delay bound.** Is there any documented or operational upper bound on the
>    delay between a change in the venue's book and delivery of that change on
>    the Markets WebSocket? If not a hard bound, is there a figure you publish or
>    stand behind (for example a p99)?
> 4. **REST as-of.** Does a `/v1/markets/{slug}/book` response carry any as-of
>    guarantee — i.e. an instant at which the returned book was true, or a
>    maximum age — and if so, which field or header states it? Can the response
>    be served from a cache (we see `cache-control: public, max-age=30`), and if
>    so, how old can the book in a cached response be?
>
> A pointer to documentation, or a sentence we may quote as the venue's stated
> behaviour, is exactly what we need. Thank you.

---

## (c) Which answers would satisfy P5, and which would not

P5 asks for a **venue-stated timing contract** on the feed the lane decides
from. The test for each answer is: does it let us bound, from the venue's own
statement, how old a received book can be?

| question | ACCEPTED as evidence for P5 | NOT accepted |
|---|---|---|
| 1 · `transactTime` | A documented statement that `transactTime` is the instant of the **last change** to that market's book (H1), **or** the instant the snapshot was **generated** (H4) with a statement of how that instant relates to the book's state — quoted from the docs or given in writing by the venue as its stated behaviour. Either lets the age be measured against a meaning. | "It's a timestamp"; an example payload; an answer that it is the last trade (H2) or the close (H3) — true but not a book-currency instant; an informal guess from a community member who is not speaking for the venue. |
| 2 · every change | A stated guarantee that with debouncing off every book change is delivered, **or** a stated debounce interval that is an upper bound. Either bounds the gap between change and message. | "Usually"; "should"; a description of throughput; a statement about ordering alone ("in sequence") — ordering is not timeliness. |
| 3 · delay bound | A published or venue-stated bound (hard, or a stated percentile the venue stands behind) on change-to-delivery delay on the Markets WebSocket. | "Pushes updates as they occur" or "real-time" (qualitative, no bound); a bound for the **institutional** streams only; a latency figure for order acknowledgements rather than market data. |
| 4 · REST as-of | A field or header the venue documents as the instant the returned book was true, or a documented maximum age of the book in a (possibly cached) response. | `Date`, `Last-Modified` or `max-age` described only as HTTP caching behaviour (already observed, and `last-modified` shown not to be a book clock); `x-pm-server-latency`; a 304 on a conditional request. |

**What does NOT clear P5, however it turns out — stated plainly:**

* **finishing this engineering work** — the calibration-only records, the
  scheduled measurement and every repair in D4 change nothing about P5;
* **connecting a Markets-WebSocket stream in the API process** — that supplies
  liveness, continuity and identity (P2–P4), not a timing contract;
* **an owner approval** — P5 is a property of the venue's protocol, and no
  internal approval can make the venue's feed documented;
* **a newer receipt timestamp** — our receipt instant bounds our own delay,
  never how old the book was when the venue sent it;
* **a moving-book experiment** — a delayed feed of a moving book moves exactly
  like a current one; movement separates live from frozen, not current from
  late (`bettor_stream_currency`, P5 `movement_does_not_establish_it`);
* **an HTTP 304, an ETag / Last-Modified, or a sequence number** — the first two
  affirm the HTTP representation (and `last-modified` has been observed moving
  over an unchanged book); a sequence number orders messages and says nothing
  about their age.

**Only the venue's documented answer (or a published contract) can clear P5.**

---

## (d) Where the answer would be recorded, and what it would change

1. **Recorded** as a new dated evidence file beside this one
   (`research/evidence/VENUE_TIMING_ANSWER_<date>.md`): the exact question, the
   exact answer quoted verbatim, who answered in what capacity, where (channel
   and link to the message or to the documentation page), and when. An answer
   that is not quotable as the venue's stated behaviour is recorded as such and
   does not count.
2. **The constant it would change:** `PRECONDITION_STATUS[P5_DOCUMENTED_TIMING]`
   in `backend/sportsassets/bettor_stream_currency.py` (`available: False` →
   `True`), with the evidence file cited in its `why`, and `TRANSACT_TIME`'s
   hypotheses in the same module resolved to the documented meaning. That flips
   `MISSING_PRECONDITIONS` to empty and `M1_STATUS` to available **as a
   mechanism** — and changes no gate by itself.
3. **And that alone is still not enough.** M1 establishes a book's currency
   only when the process that decides holds **live stream evidence** for that
   market: liveness (P2), connection continuity (P3) and instrument identity
   (P4) are runtime checks on a subscription in **that** process
   (`bettor_stream_currency.subscription_state` / `evidence_for`). The entry
   lane runs in the **API** process, which today holds **no** market-data
   subscription (`ext_pinnacle_loop.book_currency_evidence`: "the EV lane does
   not subscribe"; the stream runs in the workers process). Verified: a fresh
   process reports P2, P3, P4 and P5 all unmet. So clearing P5 would
   additionally need the API process to subscribe to the markets it decides on
   and to pass P2–P4 at decision time — a separate, reviewed change.

Until both hold, every entry-lane book read refuses
`VENUE_BOOK_CURRENCY_NOT_ESTABLISHED`, which is missing evidence and not a stale
book, and the lane records those valuations for **calibration only**
(`record_purpose = 'CALIBRATION_ONLY'`, migration 144): never admissible, no
executable price, no order.
