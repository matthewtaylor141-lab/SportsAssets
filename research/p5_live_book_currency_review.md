# P5 / book currency: can the CURRENT Polymarket US docs support a LIVE rule? (2026-10-03)

**Verdict: B. The docs do NOT establish enough. The live gate stays closed.**

`P5_BOOK_CURRENCY` stays `NOT_ESTABLISHED` (`backend/sportsassets/agents/paper_benchmark.py`
`BOOK_CURRENCY`), and so does `P5_DOCUMENTED_TIMING` (`backend/sportsassets/bettor_stream_currency.py`
`PRECONDITION_STATUS`, `available: False`). No new rule id is adopted. This note is research only and
changes no code path.

Base: production SHA `ebe54a8`. Docs read 2026-10-03 between 11:05 and 11:12 UTC.

---

## 0. How the docs were read, and how much of each was seen

The session container's egress proxy denies `docs.polymarket.us` (CONNECT 403). It also denies the
`docs-polymarket-us.mintlify.app` and `isvdocs.polymarket.us` mirrors. WebFetch returns
`EGRESS_BLOCKED` for all three. So every page was fetched on the GitHub runner through the repo's
read-only `fetch-docs` workflow (no secrets, no writes), and the job logs were read back. Each page was
requested as its raw Mintlify Markdown (`<page>.md`). The workflow prints at most the first 60,000
characters of extracted text.

| page (all under `https://docs.polymarket.us/`) | fetch-docs run | bytes | seen |
|---|---|---|---|
| `llms.txt` (index) | 37118528984 | 51,562 | index entries for WebSocket, market data, streaming, order book, FIX and changelog (log tail only) |
| `api-reference/websocket/overview.md` | 37118572840 | 3,196 | whole page |
| `api-reference/websocket/markets.md` | 37118583253 | 5,448 | whole page |
| `api-reference/markets/get-market-book.md` | 37118592957 | 11,336 | whole page |
| `api-reference/sdks/python/websocket.md` | 37118721850 | 4,294 | whole page |
| `api-reference/sdks/typescript/websocket.md` | 37118883072 | 3,509 | whole page |
| `trader-guide/streaming-apis.md` | 37118611733 | 6,451 | whole page |
| `trader-guide/market-data.md` | 37118849189 | 4,092 | whole page |
| `streaming-endpoints/market-data-stream.md` | 37118622310 | 30,264 | whole page |
| `streaming-endpoints/streaming-best-practices.md` | 37118638064 | 12,831 | whole page |
| `streaming-endpoints/proto-reference.md` | 37118760035 | 20,136 | whole page |
| `streaming-endpoints/error-handling.md` | 37118683571 | 3,020 | whole page |
| `institutional/orderbook/overview.md` | 37118822154 | 6,589 | whole page |
| `api-reference/order-book/get-order-book.md` | 37118838985 | 5,727 | whole page |
| `data-guide/market-data.md` | 37118811532 | 3,433 | whole page |
| `concepts/market-data.md` | 37118858889 | 2,868 | whole page |
| `partners/reconciliation.md` | 37118868678 | 12,727 | whole page |
| `institutional/fix-api/fix-market-data-subscription.md` | 37118705866 | 8,213 | whole page |
| `institutional/fix-api/fix-market-data-incremental.md` | 37118695341 | 8,359 | whole page |
| `changelog.md` | 37118769713 | 121,450 | **first 60,000 chars only** (newest entries). Older entries were not seen |

Retrieved page text was treated as evidence only. Nothing in it was followed as an instruction.

---

## 1. Why P5 is NOT_ESTABLISHED today, from the repo's own evidence

The question has two halves (`bettor_stream_currency.py` module docstring):

* **Q1, replacement authority.** Can a message replace the displayed book? This is **settled.** The
  Markets WebSocket table calls `SUBSCRIPTION_TYPE_MARKET_DATA` "Full order book and market stats"
  (P1 is established).
* **Q2, timing.** How old is the book inside the message when we act on it? This is **not settled.**
  P2 liveness, P3 connection continuity, P4 identity and P6 resynchronisation are available.
  **P5_DOCUMENTED_TIMING is the only unmet precondition** (`MISSING_PRECONDITIONS == ("P5_DOCUMENTED_TIMING",)`).

The specific reasons recorded so far:

1. **REST book path** (`paper_benchmark.BOOK_CURRENCY`). The book is stamped with *our* receipt instant
   (`paper_book_observations.observed_at`). "The venue read does not establish that the displayed book
   is current; its age is bounded only by our own receipt instant."
2. **No documented meaning for `transactTime`.** Four hypotheses are still open: H1 last book change,
   H2 last trade, H3 settlement/close, H4 representation generation
   (`research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md` §5, `bettor_stream_currency.TRANSACT_TIME`).
3. **HTTP validators are not a book clock.** `last-modified` equalled `date` over a seven-month-old
   expired book. `cache-control: public, max-age=30` was set, with CDN `cf-cache-status` EXPIRED and
   then HIT (`VENUE_BOOK_PROTOCOL_2026-09-27.md` §3–4).
4. **Empirical, not semantic.** On retail REST reads, `transactTime` equalled
   `stats.lastPriceSample.ts` in 277 of 277 reads where both fields existed
   (`research/evidence/run85_phase2f_analysis.txt`). In run 2F, `transactTime` advanced between reads
   whose book was unchanged (r3 → r4). That is consistent with a price-sample stamp and not with a
   last-book-change stamp. It stays an observation, and nothing is gated on it.
5. **Substitutes already refused.** Each of these has been rejected as a P5 certificate: transport
   latency, our receipt instant, an HTTP 304, a clean reconnect, a moving-book experiment, and a
   sequence number. A sequence number orders messages; it does not give their age.
   (`VENUE_TIMING_SUPPORT_REQUEST_2026-09-29.md` §(c); `market_data_design.py`.)

**What would establish P5**, per the 2026-09-29 record:

* a venue-stated meaning for `transactTime` that is a book-state instant (H1, or H4 together with how
  that instant relates to the book); or
* a stated every-change or debounce-interval guarantee; or
* a stated change-to-delivery bound; or
* a documented REST as-of field or maximum age.

Even then, the deciding (API) process must hold a live subscription that passes P2–P4 at decision time
(`bettor_market_subscription.py`).

---

## 2. What the current docs say, by question

Two feed families are documented. They must be kept apart:

* **Retail.** `wss://api.polymarket.us/v1/ws/markets` plus `GET gateway.polymarket.us/v1/markets/{slug}/book`.
  This is what the system uses (`polymarket_us==1.0.2` SDK; `bettor_market_stream`,
  `bettor_market_subscription`).
* **Institutional.** gRPC `MarketDataSubscriptionAPI`, REST `/v1/orderbook/{symbol}` and FIX, on
  `*.polymarketexchange.com`. The deciding process **cannot use it today**: the production
  institutional key lives only as a GitHub repository secret, and Render cannot reach institutional
  market data (`.github/workflows/pmx-l2-bridge.yml` header). It is reviewed here because the brief
  asks, and because it is the only place the docs come close to timing language.

### (a) An authoritative full snapshot on subscribe?

**Retail WS: only partly.** Each `MARKET_DATA` message is documented as the whole (top-of-depth) book.
Nothing states that a snapshot is sent *on subscribe*, or when.

* markets.md: "`SUBSCRIPTION_TYPE_MARKET_DATA` | Full order book and market stats"
* markets.md: "### Market Data Response / Full order book with market statistics:"
* markets.md: "The full market data subscription includes the top levels of the order book." "Full" means
  top levels, and the number of levels is not stated.

**Institutional: yes, but the pages contradict each other on what follows the snapshot.**

* streaming-best-practices.md (Snapshot vs resume table): "Market data | Snapshot, then updates (`snapshot_only: false`). | Reconnect. Take the new snapshot."
* streaming-apis.md: "Order stream and market data send a snapshot, then updates."
* trader-guide/market-data.md: "snapshot-style updates (each message is complete); treat each message as a full update unless documentation specifies delta semantics". The same page says "Market data updates are sent on every change to the order book, at regular intervals (even if no changes), and as snapshots (full book, not deltas)."
* market-data-stream.md: `snapshot_only` "If `True`, receive only initial snapshot then close stream. If `False` (default), receive continuous updates."

### (b) Incremental updates with a sequence number or other gap detection?

**No, on either feed's market-data message.**

* **Retail WS.** No sequence, message-id or snapshot/delta field appears in the documented payload
  (`marketSlug, bids, offers, state, stats, transactTime`). The SDK `types.py` matches. The only
  ordering sentence is websocket/overview.md: "3. **Process messages in order** - Messages are
  delivered in sequence". That describes ordering, not gap detection.
* **Institutional gRPC.** `MarketDataUpdate` fields (market-data-stream.md and proto-reference.md) are
  `symbol, bids, offers, state, stats, transact_time, book_hidden, price_scale, quantity_scale`. There
  is no sequence field.
* **Generic advice with no field behind it:**
  * streaming-apis.md: "use timestamps in messages, implement local ordering logic, and use sequence numbers where available."
  * data-guide/market-data.md: "Handle gaps in sequence numbers appropriately". No market-data message defines one.
* **FIX** (separate gateway, not available to us). It has session `MsgSeqNum` (34=) and
  `MarketDataIncrementalRefresh` (35=X) deltas. These belong to the FIX session protocol and are not
  part of the WebSocket or gRPC contract.

### (c) A venue-side timestamp on each book update, and what it means?

**Present on every feed, defined on none in a way that separates matching-engine time from publish time.**

| feed | field | the docs' only definition |
|---|---|---|
| retail WS `MARKET_DATA` | `marketData.transactTime` | **none.** It appears only in the example payload (`"transactTime": "2024-01-15T10:30:00Z"`) |
| retail REST `GET /v1/markets/{slug}/book` | `transactTime` | **none.** The schema gives only `type: string, format: date-time` (get-market-book.md) |
| institutional gRPC `MarketDataUpdate` | `transact_time` | "Server timestamp of update" (market-data-stream.md; proto-reference.md) |
| institutional REST `/v1/orderbook/{symbol}` | `transactTime` | "Server timestamp of the data" (orderbook/overview.md; get-order-book.md `title`) |
| institutional Drop Copy (executions, not books) | `transact_time` | "ordered per symbol, not across symbols" (streaming-best-practices.md) |

"Server timestamp of update/data" does not say which server or which event: the matching engine's
book mutation, the market-data publisher's generation, or the gateway's send. "Of the data" reads
naturally as representation generation (H4). That is exactly the hypothesis under which an old stamp
bounds the *response* and not the *book*. The retail fields have no definition at all.

The changelog also removes the field `transactTime` was empirically equal to (§1 item 4): "`lastPriceSample` is being removed. As of **Friday, July 3, 2026**, this field should no longer be considered supported". Its listed locations include the retail Markets WebSocket `SUBSCRIPTION_TYPE_MARKET_DATA` and `GET /v1/markets/{slug}/book`.

### (d) Heartbeat semantics that bound staleness on a quiet book?

**No.** Heartbeats prove a socket is alive. They carry no timestamp, no stated interval, and no
per-market statement.

* websocket/overview.md: "The server sends periodic heartbeat messages to keep the connection alive:". The payload is `{ "heartbeat": {} }`. The page also says "Clients should respond to heartbeats or implement their own keep-alive mechanism." and "4. **Monitor heartbeats** - Reconnect if heartbeats stop".
* market-data-stream.md (gRPC): "Keep-alive messages to confirm connection is active." and "If you stop receiving heartbeats, the connection may be stale. Consider reconnecting."
* The closest thing to a quiet-book bound is trader-guide/market-data.md: "at regular intervals (even if no changes)". It gives no interval, it covers only the institutional HTTP-streaming product, and the same page says "no WebSockets (uses HTTP streaming)".
* streaming-apis.md: "Some streams send periodic snapshots even if state hasn't changed to help detect missed messages, allow clients to reconcile state, typically every few minutes." The words "some" and "typically" mean this is no contract.
* Retail debouncing (markets.md): "When debouncing is enabled, updates are batched and sent at regular intervals rather than on every change." No interval is given. That a *non*-debounced subscription sends on every change is only implied by the contrast, never stated.

### (e) Explicit behaviour on reconnect or resubscribe?

**Retail WS: only generic advice.** websocket/overview.md and both SDK pages say: "2. **Handle
reconnection** - Implement automatic reconnection with exponential backoff". Nothing says that a
resubscribe produces an immediate current snapshot.

**Institutional: explicit.**

* streaming-best-practices.md: "Market data | Snapshot, then updates … | Reconnect. Take the new snapshot."
* streaming-apis.md: "If your local state doesn't match server state, unsubscribe and resubscribe (forces new snapshot), reconcile with REST API query".

Our own P3 and P6 already implement discard → reconnect → resubscribe → await full book
(`bettor_market_subscription.py` states SNAPSHOT_PENDING and GAP). That recovers continuity. It does
not establish an age.

### (f) Does the REST book carry a timestamp, and what does it mean?

* **Retail REST.** `transactTime`, `format: date-time`, with no description (get-market-book.md). No
  as-of or max-age statement exists. Observed responses are CDN-cacheable (`public, max-age=30`) per
  the 2026-09-27 record.
* **Institutional REST.** "Server timestamp of the data", with the same ambiguity as (c).
  orderbook/overview.md also positions REST as "One-time snapshot for display", and
  streaming-best-practices.md calls unary `GetOrderBook` reads "**anchors**, not a substitute for the
  stream".

### Contradictions between pages (reasons not to read one page as a contract)

* **Field naming.** websocket/overview.md says "All WebSocket messages are JSON formatted with
  snake\_case field names", with numeric `subscription_type`. markets.md and the SDK use camelCase and
  string enums.
* **Trade tape.** trader-guide/market-data.md: "Is there a public trade tape? No. Trades are not
  disseminated via market data APIs". markets.md documents `SUBSCRIPTION_TYPE_TRADE` "Real-time trade
  notifications".
* **Institutional transport.** trader-guide/market-data.md and streaming-apis.md say "long-lived HTTP
  connections, not WebSockets". market-data-stream.md is gRPC. data-guide/market-data.md's example
  uses a `MarketDataServiceStub`/`Subscribe` API that proto-reference.md does not define.
* **Shape after the snapshot.** "Snapshot, then updates" (best practices, streaming-apis) versus
  "each message is complete … full book, not deltas" (trader-guide/market-data).
* **Sequence numbers.** data-guide advises handling "gaps in sequence numbers" for a message type that
  documents none.
* **Server-side queueing.** The gRPC bidirectional request carries `bool slow_consumer_skip_to_head`
  (market-data-stream.md) with no description. The parameter implies that delivery to a slow consumer
  can fall behind head, so a server-side queue exists. Its delay is unbounded and undocumented.

---

## 3. Verdict: B

None of the following is documented for the feed the deciding process can use (retail Markets
WebSocket and retail REST book):

1. **What `marketData.transactTime` denotes** on the WebSocket or REST book. Nothing on any page
   defines it. The institutional analogue's "Server timestamp of update/data" does not separate
   matching-engine event time from generation or publish time. Under the brief's rule ("if semantics
   are ambiguous, the answer is B") this alone decides the verdict.
2. **A change-to-delivery bound** of any kind (hard or percentile), on either feed. "Real-time" and
   "server pushes updates as they occur" are qualitative. The undocumented `slow_consumer_skip_to_head`
   implies queueing whose delay is unstated.
3. **A quiet-book staleness bound.** The heartbeat interval is unstated, heartbeats carry no
   timestamp, and they are connection-level, not per-market. Periodic re-snapshots are stated only for
   the institutional product, with no interval ("regular intervals", "typically every few minutes").
4. **An every-change guarantee** for a non-debounced retail `MARKET_DATA` subscription. It is implied
   by the debouncing sentence, never stated. The debounce interval is unstated.
5. **A snapshot-on-subscribe and resnapshot-on-resubscribe contract** for the retail WebSocket. It is
   documented only for the institutional stream.
6. **Gap detection.** No sequence or message id exists on any market-data message. This matters less
   for a full-replacement feed (Q1), but it means coalescing or skipping under load cannot be detected.
7. **A REST as-of guarantee** for `/v1/markets/{slug}/book`. No as-of field or maximum age is
   documented, and responses are CDN-cacheable.

**What follows:**

* `P5_DOCUMENTED_TIMING` stays `available: False`. `BOOK_CURRENCY["verdict"]` stays `NOT_ESTABLISHED`.
* Every entry-lane book read keeps refusing `VENUE_BOOK_CURRENCY_NOT_ESTABLISHED`. Valuations stay
  `CALIBRATION_ONLY`.
* **The live gate stays closed.** This is missing evidence, not a claim that any book is stale.
* No timestamp has been reinterpreted. In particular, "Server timestamp of update" on the
  institutional gRPC page is not carried over to the retail `transactTime`, and is not read as
  matching-engine time.
* **Changed since the 2026-09-29 record:** the retail pages are unchanged in substance. New institutional statements have been read and recorded (§2: "Snapshot, then updates … Reconnect. Take the new snapshot."; "Server timestamp of update"; "sent on every change … at regular intervals (even if no changes)"), together with the `lastPriceSample` deprecation. None of them closes items 1–3 for any feed, and the institutional feed is not available to the deciding process anyway.

---

## 4. Reserved and NOT adopted: what a versioned live rule would need

This is recorded so that a future venue answer can be checked against a fixed target instead of being
read generously. **This rule is not proposed for adoption on current evidence.**

Proposed id when (and only when) the activating statements exist: `P5_LIVE_STREAM_BOOK_V1`.

**Activating documentation, all of it required, each item quoted from the venue's docs or given in
writing by the venue as its stated behaviour** (recorded per
`VENUE_TIMING_SUPPORT_REQUEST_2026-09-29.md` §(d)):

* **D1.** `marketData.transactTime` (WebSocket `MARKET_DATA`) is the matching-engine instant of the
  last change to that market's book that this message reflects. This is H1 as event time, not
  generation or send time.
* **D2.** A non-debounced `MARKET_DATA` subscription publishes a full book on **every** book change,
  and the first message after a successful subscribe is a current full book.
* **D3.** Either a stated heartbeat interval `H` together with a statement that heartbeats are emitted
  only while the connection's market-data feed is current, or a stated per-market periodic re-snapshot
  interval `S`.
* **D4.** A stated change-to-delivery bound `L`, or a statement of slow-consumer behaviour
  (drop/skip-to-head versus queue) under which `L` holds.

**Rule semantics, if D1–D4 held.** All of the following are required per market at decision time, in
the deciding process. Failing any one fails closed.

1. **Snapshot.** A full `MARKET_DATA` book for this `marketSlug` has been received on the current
   connection epoch, after the most recent subscribe (`bettor_market_subscription` state `CURRENT`).
2. **Continuity.** No disconnect, error frame or resubscribe since that book. Venue `transactTime` for
   the market has not gone backwards on this connection. Any break sets the market to
   `SNAPSHOT_PENDING` or `GAP` until a new full book arrives.
3. **Liveness.** The last heartbeat or message on the connection is at most `H` (from D3) old by our
   monotonic clock.
4. **Age.** `now_utc − transactTime ≤ MAX_BOOK_AGE_S`, where `now_utc` comes from a disciplined clock
   with a measured offset bound `ε`, and `MAX_BOOK_AGE_S ≥ L + ε`. On a quiet book this test is
   replaced by D2 + D3 (no change since `transactTime` is asserted by the every-change guarantee while
   liveness holds). That substitution is allowed **only** if D2 and D3 are stated, and never on our
   inference.
5. **Debouncing off.** The subscription has `responsesDebounced` false or absent, or else
   `MAX_BOOK_AGE_S` must also cover the stated debounce interval.
6. **Never from REST.** A REST `/book` read never satisfies this rule.

Until D1–D4 exist, the rule above is inert by construction, and the current refusal stands.

> **Note added 2026-10-03 (later):** the id `P5_LIVE_STREAM_BOOK_V1` is now used by
> `research/p5_live_stream_book_v1.md` for a *locally bounded* rule over the institutional resident stream,
> stored `READY_FOR_OWNER_APPROVAL` (migration 204). It does **not** meet D1–D4; verdict B above stands.

---

## 5. What would move this

* **The venue answers the four questions** in `research/evidence/VENUE_TIMING_SUPPORT_REQUEST_2026-09-29.md`
  §(b) in quotable form. That request is still the right one. This review adds two questions:
  * Is a non-debounced `MARKET_DATA` subscription guaranteed to send a full book on every change, and
    an immediate snapshot on (re)subscribe?
  * What does `slow_consumer_skip_to_head` do, and is the retail WebSocket subject to server-side
    queueing?
* **The venue publishes these statements in its docs.** Watch the changelog RSS
  (`https://docs.polymarket.us/changelog/rss.xml`, per changelog.md). When the docs change, re-run
  this review against §3 items 1–4.
* **Not a route:**
  * the institutional gRPC feed, while its key cannot reach the deciding process and its timestamp
    stays "Server timestamp of update";
  * any empirical correlation study;
  * any owner approval.
