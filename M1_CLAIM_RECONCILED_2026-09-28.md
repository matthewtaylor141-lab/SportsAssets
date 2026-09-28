# The M1 claim, reconciled against evidence already on file — 2026-09-28

**The owner's instruction:** *"Identify any new evidence supporting that reversal. Otherwise withdraw it and restore the narrower conclusion: the current predicate does not qualify the production feed."*

## There is no new evidence. The reversal is withdrawn.

I searched for any observation captured today that would support it. There is none: today's work touched fee commissions, partial-exit accounting, the fair-value chain and the venue's order/combo/RFQ API surface. **Not one of those reads the market-data protocol.** I did not fetch the WebSocket page, run a stream probe, or observe a payload. I asserted a protocol conclusion without doing any protocol work.

---

## What I said today, and why each half is wrong

> *"M1 was VERIFIED against the shipped market-data client and it cannot establish currency on this feed: the payload carries no sequence number (so a dropped message is undetectable) and nothing distinguishes a snapshot from an increment (so a received message is not known to be a whole book)."*

**Half one — "no sequence number, so a dropped message is undetectable."** This reinstates a precondition that `bettor_stream_currency` explicitly withdrew. Its own text:

> *"MISSING SEQUENCE NUMBERS MEAN DIFFERENT THINGS TO EACH. For a DELTA stream a sequence is load-bearing: a dropped increment leaves a book assembled from fragments and no way to know it. For a stream of self-contained FULL REPLACEMENTS it is not: a subsequent authoritative replacement does not need every intermediate change reconstructed, because it does not build on them."*
>
> *"my P3 was requiring gap-free delta continuity OF A FEED THAT HAS NO DELTAS. That was the wrong shape of requirement, imposed on a snapshot feed, and it is withdrawn as a precondition."*

**Half two — "nothing distinguishes a snapshot from an increment."** This is not merely unsupported, it is incoherent on this feed. **There are no increments to distinguish a snapshot from.** The published subscription table, read on the runner 2026-09-27 (run 36332797806, preserved in `research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md`), states:

> `SUBSCRIPTION_TYPE_MARKET_DATA` → **Full order book and market stats**

and the same page, searched sentence by sentence, contains **zero sentences** on delta / incremental / diff / partial update, and **zero** on sequence / ordering / gap / missed message. Every message is documented as the full order book. **P1 replacement authority is ESTABLISHED, not missing.**

## The machine-readable verdict settles it

```
M1_STATUS             = M1_NOT_AVAILABLE_ON_THIS_FEED
MISSING_PRECONDITIONS = ('P5_DOCUMENTED_TIMING',)
```

**One** unmet feed-level precondition, and it is timing. Not sequencing. Not snapshot identification. The module's own refusal text even carries the caution I ignored: *"NOTE the shape of the gap: replacement authority (P1) IS established…"*

## The restored conclusion

> **Our current M1 predicate does not qualify the production feed, because P5 (documented timing) is unestablished: nothing in the published protocol states how current a market-data message is — no as-of instant, no latency bound, no staleness contract.** P6 (resynchronisation) is also unestablished. Replacement authority, liveness and instrument identity are available.

**This is narrower than what I wrote, in two ways that matter.** The feed *is* fit to hold a displayed book — so the gap is not "this feed cannot be used", it is "our predicate asks for a timing guarantee this protocol does not publish". And the gap is a property of **our predicate meeting the published contract**, not a permanent venue limitation.

### The distinction the owner drew, and why it is the load-bearing one

*"Do not weaken currency requirements, but do not treat a previously rejected premise as a permanent venue limitation."*

| | |
|---|---|
| **Permanent venue limitation** | forecloses the work. Nothing to do but wait for the venue. |
| **Our predicate does not qualify this feed** | leaves three routes open, none of which weakens the requirement |

The three routes, unchanged by anything today:

1. **The venue publishes a timing contract** — an as-of instant, a latency bound, or a documented meaning for `transactTime`. That closes P5 directly. `transactTime` has **four open hypotheses** (last book change, last trade, settlement/close, representation generation) and **none is preferred**; a 219-day-old observed value is consistent with all four on an expired, zero-depth, cached market, so the observations cannot separate them.
2. **M2** — a validator on the book path (ETag / Last-Modified) answered 304. Unknown per read, reported by the clock probe.
3. **Connection continuity (P3 as replaced)** — a real, narrower requirement about **the gap in OUR observation**: the client does not reconnect, resubscribe or resynchronise (`base._message_loop` emits `close` and returns), so state is discarded on a drop rather than aged. That is our engineering, not the venue's.

Route 3 is ours to do and I had it buried under a claim that nothing could be done.

## What I am NOT doing

- **Not** weakening the currency predicate. P5 stays required. A book whose currency is not established still refuses, and the funded exit still refuses with it.
- **Not** claiming M1 can be made to work. P5 is genuinely unestablished today.
- **Not** treating the four `transactTime` hypotheses as narrowed. They are not.

## The defect this found, and where I got the wrong words

I did not invent that sentence. I **quoted `ext_pinnacle_loop.book_currency_evidence`'s `why_none` string verbatim**, and that string states the withdrawn premise:

> *"the payload carries no sequence number (so a dropped message is undetectable) and nothing distinguishes a snapshot from an increment"*

So two modules disagree, and the one an operator reads is the stale one. `bettor_stream_currency` records `MISSING_PRECONDITIONS = ('P5_DOCUMENTED_TIMING',)`; the loop's user-facing explanation asserts sequencing and snapshot identification. **That is a real defect** — a status string that contradicts the module that owns the question — and it propagated into a test and two documents today because I trusted the nearer text instead of the one that had settled it.

**Repair, queued behind the gate** (the application trees are frozen while the matched runs are in flight):

1. `book_currency_evidence.why_none` restated from `bettor_stream_currency`'s actual verdict rather than a hand-written parallel account.
2. A test asserting the two cannot drift — the loop's explanation must name the same missing preconditions the module reports.
3. `test_the_seam_supplies_no_mechanism_and_says_why_verifiably` corrected: it currently asserts the withdrawn premise appears in the string, which is a test **pinning the error in place**.
4. The two documents corrected: `WHERE_THE_FAIR_VALUE_CHAIN_BREAKS_2026-09-28.md` and `FUNDED_APPROVAL_PACKAGE.md`.

Item 3 is the worst of the four. A test that asserts a wrong claim is how a wrong claim survives review.
