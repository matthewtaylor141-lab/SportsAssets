# P5_LIVE_STREAM_BOOK_V1: a versioned live book-currentness rule, ready for owner approval (2026-10-03)

**Status: `READY_FOR_OWNER_APPROVAL`. Not approved, and no agent may approve it.** Production admits no
actual order under this rule. `actual_admission.APPROVED_LIVE_BOOK_RULES` stays empty, and the stored
artifact row has no owner record.

| | |
|---|---|
| rule id / version | `P5_LIVE_STREAM_BOOK_V1` / `1` |
| canonical text | `backend/sportsassets/live_book_currency.py` `DOCUMENT`, hashed as `canonical_json` (sorted keys, separators `(',', ':')`, ASCII) |
| sha256 | `b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a` |
| stored in | `live_rule_artifacts` (migration `204_live_rule_artifacts.sql`), status `READY_FOR_OWNER_APPROVAL`, `created_by = 'migration 204'` |
| pinned by | `tests/test_live_book_currency.py`, `tests/test_live_rule_artifacts.py` (both on the capital-critical list) |

This note does **not** reverse `research/p5_live_book_currency_review.md`. Verdict B on *documented*
timing stands. The venue still documents no change-to-delivery bound, no heartbeat interval, no debounce
interval and no sequence number. `P5_DOCUMENTED_TIMING` stays unavailable, and the paper label
`paper_benchmark.BOOK_CURRENCY` stays `NOT_ESTABLISHED`.

The review reserved a documented-timing rule that needs D1–D4 from the venue. This rule does not meet
D1–D4. It is the strongest rule that **our own evidence** can bound. Everything it cannot bound is listed
by name. Approving it means the owner accepts those listed residuals for the bounded actual lane. It
does not claim that the venue guarantees currency.

---

## 1. Rule text: what must hold at the decision instant, in the deciding process

The rule applies to a book read from the resident institutional gRPC market-data stream
(`institutional_stream.current(symbol)`), for the institutional symbol that is **exactly** mapped to the
retail contract being decided. A REST book read never qualifies.

| id | requirement | fails as |
|---|---|---|
| C1 | **Exact contract identity.** The identity mapper returned `EXACT` with a symbol, and the evaluated book is that symbol's. | no mapping → `NOT_ESTABLISHED` (`IDENTITY_MAPPING_NOT_ESTABLISHED`). Another symbol or not `EXACT` → `REFUSED` |
| C2 | The stream runs in this process. | not running → `NOT_ESTABLISHED`. Venue refused the credential → `REFUSED` |
| C3 | **Connection epoch alive.** The connection is open and has an epoch number. | no epoch → `NOT_ESTABLISHED`. Book held when the epoch ended → `GAP` |
| C4 | **A complete book on THIS epoch** after the last connect, reconnect or gap, with no gap open. | gap, or a book from an earlier epoch → `GAP`. Nothing received yet → `NOT_ESTABLISHED` |
| C5 | **Sequence integrity.** Recorded as `SEQUENCE_NOT_PROVIDED_BY_VENUE`. Integrity rests on C3, C4 and C7 (epoch plus monotonic venue timestamp). | n/a |
| C6 | Venue `transact_time` present. | `NOT_ESTABLISHED` |
| C7 | `transact_time` monotonic within the epoch. A regression is a gap until an update arrives at or past the high-water mark. | `GAP` |
| C8 | **Local receipt age** `now − receipt ≤ 2.0 s` (our clock). | above → `STALE`. Absent, or more than 0.25 s in the future → `NOT_ESTABLISHED` |
| C9 | **Clock-skew bound** `|receipt − transact_time| ≤ 2.0 s`. | venue stamp older → `STALE`. Venue stamp ahead → `NOT_ESTABLISHED` |
| C10 | **Market open:** instrument state `INSTRUMENT_STATE_OPEN`. | unknown → `NOT_ESTABLISHED`. Any other state → `REFUSED` |
| C11 | The stream's own `current()` answered ok. Its refusal code is classified, never ignored. | hidden, crossed or symbol refused → `REFUSED`. Silent or old → `STALE`. Gap codes → `GAP`. Otherwise `NOT_ESTABLISHED` |
| C12 | **Priced from this book.** The decision's executable price and depth were read from the same stream observation (same epoch, same receipt instant). | `NOT_ESTABLISHED` (`PRICED_BOOK_IS_NOT_THE_EVALUATED_STREAM_BOOK`) |
| C13 | **Verdict age at submit.** The actual lane submits only while `now − evaluated_at ≤ 2.0 s`. | the lane refuses `LIVE_BOOK_VERDICT_STALE_AT_ACTUAL_SUBMIT` |

**Verdict.** `ESTABLISHED` only when C1–C12 all pass. Otherwise the verdict is the most severe failing
class, in the order `REFUSED > GAP > NOT_ESTABLISHED > STALE`. The output carries every component, plus
`stream_book_verdict` (the verdict before C12 binds it to the priced book), `subscription_state`,
`connection_epoch`, `receipt_age_s`, `venue_ts_age_s`, `venue_receipt_skew_s` and `gap_since_snapshot`.

### Bounds and why (part of the hashed text)

* **Receipt age ≤ 2 s.** This is five times tighter than the 10 s receipt bound both lanes apply to the
  REST book. It is also well inside the stream's own 15 s liveness bound and 30 s snapshot bound. No
  heartbeat or re-snapshot interval is documented, so only a recent message for the symbol bounds a quiet
  book. Quiet books therefore fail closed as `STALE`. That cost is accepted.
* **|venue ts − receipt| ≤ 2 s.** On an NTP-disciplined host with a healthy stream, these are
  milliseconds apart. A gap beyond 2 s means either a queued (slow-consumer) delivery or a clock fault,
  and both refuse. The check bounds the *sum* of delay and clock offset, not either one alone.
* **Verdict age at submit ≤ 2 s.** Without this, the actual lane's 10 s decision-age allowance would let
  a book that was 2 s current reach the venue 12 s old.

---

## 2. The evidence split

### venue_guaranteed: only what the cited pages state

These quotes are verbatim as recorded in the 2026-10-03 review (pages read 11:05–11:12 UTC).

| id | statement | quote · URL |
|---|---|---|
| VG1 | A snapshot is sent after subscribe and after reconnect | "Market data \| Snapshot, then updates (`snapshot_only: false`). \| Reconnect. Take the new snapshot." · https://docs.polymarket.us/streaming-endpoints/streaming-best-practices. Also "Order stream and market data send a snapshot, then updates." · https://docs.polymarket.us/trader-guide/streaming-apis |
| VG2 | Each message is a complete book, not a delta | "snapshot-style updates (each message is complete); treat each message as a full update unless documentation specifies delta semantics" and "as snapshots (full book, not deltas)" · https://docs.polymarket.us/trader-guide/market-data. *Caveat:* other pages say "updates" without defining the word. No page documents delta semantics |
| VG3 | `transact_time` is a server timestamp | "Server timestamp of update" · https://docs.polymarket.us/streaming-endpoints/market-data-stream and https://docs.polymarket.us/streaming-endpoints/proto-reference. *Caveat:* the page does not say which server or which event |
| VG4 | Updates continue while subscribed | "If `False` (default), receive continuous updates." · https://docs.polymarket.us/streaming-endpoints/market-data-stream |
| VG5 | Heartbeats are connection keep-alives | "Keep-alive messages to confirm connection is active." and "If you stop receiving heartbeats, the connection may be stale. Consider reconnecting." · https://docs.polymarket.us/streaming-endpoints/market-data-stream. *Caveat:* no interval, no timestamp, no per-market meaning |
| VG6 | There is no sequence number | The documented `MarketDataUpdate` fields are symbol, bids, offers, state, stats, transact_time, book_hidden, price_scale and quantity_scale (market-data-stream, proto-reference). Recorded as `SEQUENCE_NOT_PROVIDED_BY_VENUE` |

### locally_bounded: what our receipt clock and epoch logic bound

* **LB1 connection epoch.** Any disconnect discards every held book as `GAP`. Only a complete book on a
  *later* epoch clears it. A book from an earlier epoch is never aged into currency.
* **LB2 complete book after a connect, reconnect or gap.**
* **LB3 receipt age ≤ 2 s.** This is the only bound on a quiet book.
* **LB4 venue timestamp:** present, monotonic within the epoch, and |receipt − ts| ≤ 2 s. This bounds
  delay plus offset jointly.
* **LB5 market open,** with the stream's checks passing: not hidden, not crossed, scales known.
* **LB6 exact identity.** The mapper's EXACT answer for this slug.
* **LB7 priced from this book.**
* **LB8 verdict age at submit ≤ 2 s.**

### not_established: what neither the venue nor our logic bounds

* **NE1 undelivered changes.** Changes after the message's server stamp that have not yet been delivered
  are unbounded. No change-to-delivery bound exists, and the undocumented `slow_consumer_skip_to_head`
  implies queueing.
* **NE2 debouncing and coalescing.** "Sent on every change to the order book" appears only on
  trader-guide/market-data, which describes an HTTP-streaming product. The retail debounce interval is
  unstated.
* **NE3 quiet-book staleness** is bounded only by requiring a recent message for this symbol, because no
  heartbeat interval is documented.
* **NE4** which event `transact_time` stamps: matching engine, publisher or gateway.
* **NE5** the host clock's offset from venue time is not measured. LB4 bounds offset plus delay.
* **NE6** loss inside one epoch is undetectable without a sequence number. Full replacement means the
  latest complete book supersedes any lost one.
* **NE7** whether an order entered through the retail route executes against the same book the
  institutional stream publishes. That is the identity mapping's claim, not this rule's.

---

## 3. How it is wired, fail closed

* **Pure rule:** `sportsassets/live_book_currency.py`. It contains `evaluate`, `admission_record` and
  `verdict_age_refusal`, plus `DOCUMENT`, `SHA256` and `CODE_RULES`.
* **Approved set:** `sportsassets/live_rule_artifacts.py` `approved_live_book_rules(conn)` returns the
  code constant (empty) ∪ the rule ids of rows that meet every condition below:
  * `status = 'APPROVED'`;
  * a non-agent owner record (actor, `approved_at`, statement), where the actor is not the creator;
  * `(version, sha256)` equal to the code's `CODE_RULES`.

  A missing table, a missing row, a hash mismatch or any error yields the empty constant. The read runs
  in a savepoint, so it never poisons the caller's transaction. `actual_admission` stays pure.
* **Admission:** `execution_intent.create` and `ActualLane._run` both read that set and pass it as
  `actual_admission.evaluate(approved_book_rules=...)`. The lane reads it again immediately before the
  claim, so a superseded approval or a moved code hash refuses there. The lane also enforces C13.
* **Decision path:** `execution_intent.start` installs `decision_hooks.LIVE_BOOK_EVIDENCE =
  live_book_evidence.for_decision`. `paper_benchmark` calls it when it writes the intent's admission
  facts. `paper_benchmark` never imports the live modules; its import guard still holds.
  * The identity mapper is **injectable**: `live_book_evidence.IDENTITY_MAPPER`, or
    `ctx["live_book_identity"]`, a callable `mapper(slug, order_intent) -> {"status": "EXACT",
    "symbol": ...}`. The stream read uses the public `institutional_stream.current()`.
    `institutional_stream.py` is not edited.
  * **No mapper (today):** no stream read happens. `admission_facts.book.book_currency` stays the REST
    path's `BOOK_CURRENCY`, and the live record (`NOT_ESTABLISHED`, `IDENTITY_MAPPING_NOT_ESTABLISHED`)
    is kept beside it as `live_book_currency`.
  * **EXACT mapper:** the live record *becomes* `book_currency`, and the paper label is kept as
    `paper_book_currency`. The decision is still priced from the REST paper book, so C12 makes the
    verdict `NOT_ESTABLISHED`. `stream_book_verdict` shows what the stream book alone would be. Paper
    behaviour, its `BOOK_CURRENCY` label and its REST price source are unchanged.

**What still has to happen before any order could ever be admitted under this rule.** Each item is
separate, and none of them is in this change:

1. The owner approves the artifact (§4).
2. The integrator installs workstream S's exact slug↔symbol mapper in
   `live_book_evidence.IDENTITY_MAPPER`, and the institutional stream runs in the deciding process.
3. The decision prices its executable levels from the same stream observation it certifies (C12).
4. Every other admission requirement and runtime gate holds: switch, account, cap, buying power,
   decision age and idempotency.

---

## 4. Owner approval action (never self-approved)

**There is no authenticated route that writes `live_rule_artifacts`.** No code path in the repository
inserts, updates or deletes rows there, and a test pins that. Approval is an **operator action taken by
the owner**, with the owner's own database credential, on the production database:

```sql
BEGIN;
-- 1. confirm the stored text is the reviewed one
SELECT rule_id, version, status, sha256
  FROM live_rule_artifacts
 WHERE rule_id = 'P5_LIVE_STREAM_BOOK_V1' AND version = '1';
--    expect status READY_FOR_OWNER_APPROVAL and
--    sha256 b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a
-- 2. record the approval (all three owner fields are required by CHECK)
UPDATE live_rule_artifacts
   SET status                   = 'APPROVED',
       owner_approval_actor     = '<owner name or email - never an agent>',
       owner_approved_at        = now(),
       owner_approval_statement = 'I approve P5_LIVE_STREAM_BOOK_V1 version 1, '
           'sha256 b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a, '
           'and accept its not_established residuals NE1-NE7.'
 WHERE rule_id = 'P5_LIVE_STREAM_BOOK_V1' AND version = '1'
   AND status  = 'READY_FOR_OWNER_APPROVAL'
   AND sha256  = 'b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a';
-- expect UPDATE 1
COMMIT;
```

The database refuses each of the following:

* `APPROVED` without all three owner fields;
* an actor named DEREK, XAVIER, AUDREY, CLAUDE or SYSTEM, or starting with AGENT, CLAUDE or MIGRATION;
* an actor equal to `created_by`;
* any change to the text or hash;
* a second write to the approval record;
* any backwards status move;
* a delete;
* the 204 rollback while an approval exists.

* **Check the result:** `live_rule_artifacts.describe(conn)` should list `P5_LIVE_STREAM_BOOK_V1` under
  `approved_live_book_rules` only when the deployed code's `live_book_currency.SHA256` equals the stored
  hash.
* **Withdraw an approval:** `UPDATE live_rule_artifacts SET status = 'SUPERSEDED' WHERE rule_id =
  'P5_LIVE_STREAM_BOOK_V1' AND version = '1';`. This is a permitted forward move. It takes effect at the
  next intent and at every lane run.
* **Change the rule** (a bound, a requirement, a citation): ship a new version, which means a new
  `VERSION`, a new hash, a new migration row and a new approval. An edit to V1 makes its approval inert,
  because the hashes no longer match.
