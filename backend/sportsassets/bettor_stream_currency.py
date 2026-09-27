"""CAN A MARKET-DATA SUBSCRIPTION ESTABLISH THAT A BOOK IS CURRENT? M1.

`bettor_venue_currency` names M1 -- a live market-data subscription -- as one of
two mechanisms that can establish a venue book's currency. This module decides
whether that claim holds on the feed this system is actually bound to.

TWO QUESTIONS, AND I HAD THEM FUSED
-----------------------------------

The first version of this module asked one question and answered it no. There
are two, they have different answers, and conflating them produced a conclusion
wider than the evidence:

    Q1  CAN THIS MESSAGE REPLACE THE DISPLAYED BOOK without reconstructing
        earlier updates?
    Q2  WHAT ESTABLISHES THAT THIS PARTICULAR OBSERVATION IS SUFFICIENTLY
        CURRENT to trade on?

Q1 is about the message's AUTHORITY. Q2 is about its TIMING. A feed can settle
Q1 completely and say nothing about Q2 -- which is exactly what this one does.

MISSING SEQUENCE NUMBERS MEAN DIFFERENT THINGS TO EACH. For a DELTA stream a
sequence is load-bearing: a dropped increment leaves a book assembled from
fragments and no way to know it. For a stream of self-contained FULL
REPLACEMENTS it is not: a subsequent authoritative replacement does not need
every intermediate change reconstructed, because it does not build on them.

    A CONNECTION IS STILL NOT A GUARANTEE. A heartbeat proves a socket is open.
    A recently received message proves a message arrived. Neither answers Q2,
    and treating either as though it did would be the receipt-instant error
    again with a new token in front of it.

WHAT THE PUBLISHED PROTOCOL SAYS, AND MY EARLIER REPORT OF IT WAS WRONG
----------------------------------------------------------------------

I wrote that full-book authority "rests on a DOCSTRING". It does not. The
venue's own WebSocket page -- read on the runner on 2026-09-27, run 36332797806,
preserved in research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md -- states it in
its subscription-types table:

    SUBSCRIPTION_TYPE_MARKET_DATA        Full order book and market stats
    SUBSCRIPTION_TYPE_MARKET_DATA_LITE   Lightweight price data only
    SUBSCRIPTION_TYPE_TRADE              Real-time trade notifications

The pinned SDK's docstring AGREES with the published protocol; it was never the
only source, and calling it the only source understated what the venue
documents. That correction matters because it changes a verdict.

The same page, searched sentence by sentence, contains:

    ZERO sentences on delta / incremental / diff / partial update
    ZERO sentences on sequence / ordering / out of order / gap / missed message
    ZERO sentences on heartbeat / reconnect / resubscribe / disconnect
    ZERO sentences stating any latency or as-of guarantee

So this is documented as a FULL-REPLACEMENT feed with no incremental mechanism
at all -- and my P3 was requiring gap-free delta continuity OF A FEED THAT HAS
NO DELTAS. That was the wrong shape of requirement, imposed on a snapshot feed,
and it is withdrawn as a precondition.

THE PRECONDITIONS, CORRECTED
----------------------------

  P1  REPLACEMENT AUTHORITY  (Q1)  -- ESTABLISHED by the published protocol.
      The message is documented as the full order book, so it replaces the
      displayed book without reconstructing anything.

  P2  LIVENESS               (Q2)  -- AVAILABLE. A Heartbeat message type
      exists and `bettor_market_stream` records `last_heartbeat_at`. Necessary,
      nowhere near sufficient: silence on a dead socket and a quiet market are
      indistinguishable, which is why P2 alone never admits.

  P3  CONTINUITY  -- REPLACED, not deleted. The delta-reconstruction
      requirement is withdrawn. What survives is narrower and still real:
      CONNECTION CONTINUITY. A book carried across a disconnect has an unknown
      number of unseen replacements in front of it, and the client does not
      reconnect, resubscribe or resynchronise -- `base._message_loop` emits
      'close' and RETURNS. So state is DISCARDED on a drop rather than aged.
      This is about the gap in OUR observation, not about reassembling the
      venue's stream.

  P4  IDENTITY               (Q1)  -- AVAILABLE. `marketSlug` is on the
      payload and is compared, not assumed.

  P5  DOCUMENTED TIMING      (Q2)  -- NOT ESTABLISHED, AND THIS IS THE ONE
      THAT BINDS. Nothing in the published protocol states how current a
      market-data message is: no as-of instant, no latency bound, no staleness
      contract. `transactTime` is the only candidate field, and what it denotes
      is only partly resolved (see below).

  P6  RESYNCHRONISATION      (Q2)  -- NOT ESTABLISHED. The protocol documents
      no resubscribe-and-resnapshot procedure, and the client implements none.

WHAT IS KNOWN ABOUT `transactTime`, EXACTLY
-------------------------------------------

Settled on the runner, 2026-09-27, with the discriminator stated before it ran:
a response stamp cannot precede its own response, so `transactTime` materially
before the response `Date` CONTRADICTS the response-stamp reading -- one
observation suffices in that direction and none could establish the positive
direction. Observed on two independent markets:

    date - transactTime = 18,969,408 s (219.6 days)
    across an 8 s gap: date advanced 8.0 s, transactTime advanced 0.0 s

  ESTABLISHED      `transactTime` is a MARKET-DATA instant, not a response
                   stamp.
  NOT ESTABLISHED  WHICH market-data instant. Last book change, last trade and
                   settlement all coincide on the expired zero-depth books these
                   reads landed on, so the observation cannot separate them.

That remaining question is not answerable by more observation of quiet books --
which is the trap of the inference I withdrew earlier. It needs the venue
documenting the field, or a read of a market whose book is demonstrably moving.

THE CONCLUSION, NARROWED TO WHAT THE EVIDENCE SUPPORTS
------------------------------------------------------

OUR M1 PREDICATE CANNOT QUALIFY THIS FEED, because P5 is unestablished: nothing
published states how current a message is, and a mechanism that cannot answer Q2
cannot establish currency however completely it answers Q1.

THAT IS NARROWER THAN WHAT I WROTE BEFORE, IN TWO WAYS. Replacement authority is
established, not missing -- so the feed is fit to hold a displayed book. And
"the venue has no engineering route" does not follow from "our predicate cannot
qualify it": the remaining gap is a documentation and timing question with named
ways to close it, not a demonstrated impossibility.

WHY THE MACHINERY IS BUILT ANYWAY
---------------------------------

  * the verdict is COMPUTED from live state, not hard-coded, so if the venue
    documents timing the mechanism becomes available by measurement and not by
    someone deciding it has;
  * connection-epoch invalidation is correct regardless -- see P3;
  * the same state answers "how stale is our liquidity picture", which the depth
    and sizing evidence legitimately wants.

NOTHING HERE WIDENS A BOUND. The single number it can produce, when every
precondition is met, is the age of a full snapshot for the right instrument on a
connection that has not dropped -- a stricter statement than any this lane has
made.
"""

from __future__ import annotations

import threading
import time

# ── the preconditions, named so a refusal can point at one ──────────
#
# P3's NAME CHANGED AND SO DID ITS MEANING. It was P3_GAP_FREE_CONTINUITY: a
# requirement to prove no message was lost, which needs a sequence. That is the
# right requirement for a DELTA stream and the wrong one for a stream of
# self-contained full replacements, which this is -- a later authoritative
# replacement does not build on the increments before it. The token is renamed
# rather than reused so no stored verdict can be read against the old meaning.

P1_REPLACEMENT_AUTHORITY = "P1_FULL_REPLACEMENT_AUTHORITY"
P2_LIVENESS = "P2_CONNECTION_LIVENESS"
P3_CONNECTION_CONTINUITY = "P3_CONNECTION_CONTINUITY"
P4_IDENTITY = "P4_INSTRUMENT_IDENTITY"
P5_DOCUMENTED_TIMING = "P5_DOCUMENTED_TIMING"
P6_RESYNCHRONISATION = "P6_RESYNCHRONISATION"
PRECONDITIONS = (P1_REPLACEMENT_AUTHORITY, P2_LIVENESS,
                 P3_CONNECTION_CONTINUITY, P4_IDENTITY,
                 P5_DOCUMENTED_TIMING, P6_RESYNCHRONISATION)

#: WITHDRAWN. Kept as a name so the change is visible in the record rather than
#: looking like it was never asserted.
P3_WITHDRAWN_REQUIREMENT = "P3_GAP_FREE_DELTA_CONTINUITY"
WITHDRAWN_REQUIREMENTS = {
    P3_WITHDRAWN_REQUIREMENT: {
        "withdrawn_on": "2026-09-27",
        "what_it_required": ("proof that no message was lost between the last "
                            "trusted state and now, which needs a sequence "
                            "number or equivalent"),
        "why_withdrawn": (
            "it is a DELTA-stream requirement imposed on a documented "
            "FULL-REPLACEMENT feed. The published protocol's subscription table "
            "calls SUBSCRIPTION_TYPE_MARKET_DATA 'Full order book and market "
            "stats', and the page contains ZERO sentences on deltas, "
            "increments, sequences, ordering or gaps. A subsequent "
            "authoritative full replacement need not reconstruct every "
            "intermediate change, so an absent sequence does not make the "
            "displayed book a fiction assembled from fragments"),
        "what_replaced_it": P3_CONNECTION_CONTINUITY,
        "and_what_did_not_change": (
            "the verdict. M1 still cannot qualify this feed -- but now on P5, "
            "which is a TIMING gap, rather than on a continuity gap that was "
            "never the right question"),
    },
}

#: THE QUESTION EACH PRECONDITION SERVES. Separating these is the correction:
#: a feed can settle authority completely and say nothing about timing.
Q1_REPLACEMENT_AUTHORITY = "CAN_THIS_MESSAGE_REPLACE_THE_DISPLAYED_BOOK"
Q2_SUFFICIENTLY_CURRENT = "IS_THIS_OBSERVATION_CURRENT_ENOUGH_TO_TRADE_ON"
PRECONDITION_SERVES = {
    P1_REPLACEMENT_AUTHORITY: Q1_REPLACEMENT_AUTHORITY,
    P2_LIVENESS: Q2_SUFFICIENTLY_CURRENT,
    P3_CONNECTION_CONTINUITY: Q2_SUFFICIENTLY_CURRENT,
    P4_IDENTITY: Q1_REPLACEMENT_AUTHORITY,
    P5_DOCUMENTED_TIMING: Q2_SUFFICIENTLY_CURRENT,
    P6_RESYNCHRONISATION: Q2_SUFFICIENTLY_CURRENT,
}

M1_AVAILABLE = "M1_AVAILABLE"
M1_NOT_AVAILABLE = "M1_NOT_AVAILABLE_ON_THIS_FEED"

#: THE PUBLISHED PROTOCOL, read directly rather than inferred from the SDK.
PUBLISHED_PROTOCOL = {
    "source": "https://docs.polymarket.us/api-reference/websocket/markets",
    "read_on": "2026-09-27",
    "read_by": ("the command-verify book-protocol job on the GitHub runner, "
                "run 36332797806. The build container's egress denies this "
                "host, which is why this stood open in source"),
    "evidence": "research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md",
    "subscription_types": {
        "SUBSCRIPTION_TYPE_MARKET_DATA": "Full order book and market stats",
        "SUBSCRIPTION_TYPE_MARKET_DATA_LITE": "Lightweight price data only",
        "SUBSCRIPTION_TYPE_TRADE": "Real-time trade notifications",
    },
    "states_full_order_book": True,
    "sentences_on_deltas_or_increments": 0,
    "sentences_on_sequence_or_ordering_or_gaps": 0,
    "sentences_on_heartbeat_or_reconnect": 0,
    "sentences_stating_a_latency_or_as_of_guarantee": 0,
    "what_this_settles": (
        "Q1. The message is documented as the full order book, so it replaces "
        "the displayed book without reconstructing earlier updates"),
    "what_this_leaves_open": (
        "Q2. No published sentence states how current a market-data message "
        "is, and none documents reconnect or resynchronisation"),
}

#: ── WHAT THE SHIPPED CLIENT PROVIDES. Determined by inspection, dated, and
#: carrying the artefact each conclusion rests on so a reader can check it.
FEED_CONTRACT = {
    "determined_on": "2026-09-27",
    "method": ("inspection of the installed polymarket_us.websocket module -- "
               "types.py for the payload shape, base.py for the lifecycle -- "
               "RECONCILED against the published protocol above. The SDK binds "
               "what this system can do; the published protocol states what "
               "the venue guarantees. Reading only the SDK is how I came to "
               "report a documented guarantee as a docstring"),
    "market_data_payload_fields": ["marketSlug", "bids", "offers", "state",
                                   "stats", "transactTime"],
    "has_sequence_number": False,
    "has_snapshot_or_delta_marker": False,
    "has_message_id": False,
    "has_heartbeat_message_type": True,
    "client_reconnects": False,
    "client_resubscribes": False,
    "client_resynchronises": False,
    "on_connection_closed": ("base._message_loop emits 'close' and RETURNS. "
                             "Reconnect, resubscribe and resynchronisation are "
                             "the caller's entire responsibility"),
    "sdk_and_protocol_agree_on": (
        "full-replacement semantics. MarketData's docstring says 'full order "
        "book' and so does the venue's published subscription table"),
    "sdk_and_protocol_disagree_on": (
        "nothing found. The absences -- no sequence field, no snapshot marker, "
        "no documented reconnect -- are consistent between them, which is what "
        "makes them properties of the FEED rather than of this client"),
    "full_book_claim_rests_on": (
        "the PUBLISHED subscription-types table, which the pinned SDK's "
        "docstring agrees with. I previously reported this as resting on the "
        "docstring alone, and that was wrong"),
}

#: WHAT `transactTime` DENOTES. Partly settled; the unsettled part named.
TRANSACT_TIME = {
    "determined_on": "2026-09-27",
    "run": "36333087522",
    "discriminator_stated_before_running": (
        "a response stamp cannot precede its own response, so transactTime "
        "materially before the response Date CONTRADICTS the response-stamp "
        "reading. One observation suffices in that direction; none could "
        "establish the positive direction -- which is why this is not the "
        "resampling withdrawn earlier"),
    "observed": {
        "date_minus_transact_time_s": 18969408.2,
        "across_an_8s_gap": ("date advanced 8.0 s, transactTime advanced "
                            "0.0 s, last-modified advanced 0.0 s"),
        "markets": 2,
        "market_state": "MARKET_STATE_EXPIRED",
        "book_levels": "0 bids / 0 offers on every read",
    },
    "ESTABLISHED": ("transactTime is a MARKET-DATA instant, not a response "
                    "stamp"),
    "NOT_ESTABLISHED": (
        "WHICH market-data instant. Last book change, last trade and "
        "settlement all coincide on an expired zero-depth book, so these "
        "observations cannot separate them"),
    "why_more_of_the_same_cannot_help": (
        "a quiet book and a delayed feed predict the same unchanged value. "
        "Separating them needs the venue documenting the field, or a read of a "
        "market whose book is demonstrably MOVING"),
    "what_turns_on_it": (
        "if it is the last book change, an old value means a quiet money line "
        "and a 30 s bound on it refuses every quiet market while calling it "
        "freshness -- the bound applied to the wrong quantity, which is a "
        "separate repair and NOT a loosening. If it lags a moving book, it is "
        "a genuine delay and the bound is correct"),
}

PRECONDITION_STATUS = {
    P1_REPLACEMENT_AUTHORITY: {
        "available": True,
        "serves": Q1_REPLACEMENT_AUTHORITY,
        "why": ("the PUBLISHED protocol's subscription table states "
                "SUBSCRIPTION_TYPE_MARKET_DATA is 'Full order book and market "
                "stats', and the pinned SDK agrees. A documented full "
                "replacement needs no snapshot marker: there is nothing it "
                "could be confused with, because the protocol documents no "
                "incremental message at all"),
        "corrected_on": "2026-09-27",
        "previously_said": (
            "NOT ESTABLISHED, on the grounds that the claim was a docstring. "
            "That was wrong -- the venue publishes it"),
    },
    P2_LIVENESS: {
        "available": True,
        "serves": Q2_SUFFICIENTLY_CURRENT,
        "why": ("a Heartbeat message type exists and bettor_market_stream "
                "already records last_heartbeat_at"),
        "necessary_not_sufficient": (
            "silence on a dead socket and a quiet market are "
            "indistinguishable, so P2 alone never admits"),
    },
    P3_CONNECTION_CONTINUITY: {
        "available": True,
        "serves": Q2_SUFFICIENTLY_CURRENT,
        "why": ("this module tracks connection epochs and DISCARDS every "
                "market's state on a drop, so a book is never carried across "
                "a gap in OUR observation. That is enforceable without a "
                "sequence number, because it is a fact about our own socket"),
        "this_is_not_the_withdrawn_requirement": (
            "it does not claim the venue's stream was reassembled without "
            "loss. It claims we did not hold a book across a disconnect"),
        "and_it_is_why_the_machinery_exists": (
            "correct regardless of whether M1 ever becomes load-bearing"),
    },
    P4_IDENTITY: {
        "available": True,
        "serves": Q1_REPLACEMENT_AUTHORITY,
        "why": "marketSlug is on the payload and is the venue's own identifier",
    },
    P5_DOCUMENTED_TIMING: {
        "available": False,
        "serves": Q2_SUFFICIENTLY_CURRENT,
        "why": ("nothing in the published protocol states how current a "
                "market-data message is: no as-of instant, no latency bound, "
                "no staleness contract. ZERO sentences matched. transactTime "
                "is the only candidate field and its denotation is only "
                "partly resolved"),
        "what_would_establish_it": (
            "the venue documenting transactTime's semantics or any as-of "
            "guarantee; or a read of a demonstrably MOVING book that "
            "separates last-change from now"),
        "this_is_the_binding_precondition": True,
    },
    P6_RESYNCHRONISATION: {
        "available": False,
        "serves": Q2_SUFFICIENTLY_CURRENT,
        "why": ("the protocol documents no resubscribe-and-resnapshot "
                "procedure and the client implements none -- "
                "base._message_loop emits 'close' and returns"),
        "what_would_establish_it": (
            "a documented resynchronisation procedure, plus a client that "
            "performs it. The second half is ours to build and the first is "
            "not"),
        "mitigated_not_solved_by": (
            "P3's discard-on-drop, which makes the consequence safe -- no "
            "stale book is used -- without making the mechanism available"),
    },
}

MISSING_PRECONDITIONS = tuple(
    n for n in PRECONDITIONS if not PRECONDITION_STATUS[n]["available"])

#: THE VERDICT ON THE MECHANISM ITSELF, not on any one book.
M1_STATUS = M1_AVAILABLE if not MISSING_PRECONDITIONS else M1_NOT_AVAILABLE

#: Liveness bound. A chosen allowance, labelled as one.
MAX_SILENCE_S = 15.0
#: How old a trusted snapshot may be. The same bound the rest of the lane uses;
#: this module does not introduce a second one.
MAX_SNAPSHOT_AGE_S = 30.0


def questions() -> dict:
    """THE TWO QUESTIONS, ANSWERED SEPARATELY. This is the correction.

    Fusing them is what produced a conclusion wider than the evidence: the feed
    settles authority completely and says nothing about timing, and one verdict
    covering both reported the first as missing.
    """
    def _for(q):
        names = [n for n in PRECONDITIONS if PRECONDITION_SERVES[n] == q]
        miss = [n for n in names if not PRECONDITION_STATUS[n]["available"]]
        return {"preconditions": names, "missing": miss,
                "answered": not miss}
    return {
        Q1_REPLACEMENT_AUTHORITY: dict(
            _for(Q1_REPLACEMENT_AUTHORITY),
            verdict=("ESTABLISHED. The published protocol documents "
                     "SUBSCRIPTION_TYPE_MARKET_DATA as the full order book, so "
                     "a message replaces the displayed book without "
                     "reconstructing earlier updates"),
            and_a_missing_sequence_does_not_change_it=(
                "a sequence is load-bearing for a DELTA stream, where a "
                "dropped increment leaves a book assembled from fragments. A "
                "later authoritative full replacement does not build on the "
                "increments before it")),
        Q2_SUFFICIENTLY_CURRENT: dict(
            _for(Q2_SUFFICIENTLY_CURRENT),
            verdict=("NOT ESTABLISHED, on P5. No published sentence states how "
                     "current a market-data message is, and transactTime's "
                     "denotation is only partly resolved"),
            and_this_is_not_a_claim_of_staleness=(
                "an unmeasured age is not an old one")),
    }


def mechanism_status() -> dict:
    """Whether M1 can establish anything on this feed, and what is missing."""
    return {
        "mechanism": "M1_LIVE_MARKET_DATA_SUBSCRIPTION",
        "status": M1_STATUS,
        "preconditions": {n: dict(PRECONDITION_STATUS[n]) for n in PRECONDITIONS},
        "missing": list(MISSING_PRECONDITIONS),
        "the_two_questions": questions(),
        "published_protocol": PUBLISHED_PROTOCOL,
        "feed_contract": FEED_CONTRACT,
        "transact_time": TRANSACT_TIME,
        "withdrawn_requirements": WITHDRAWN_REQUIREMENTS,
        "what_a_subscription_here_does_prove": (
            "that a socket is open, that a message for a named instrument "
            "arrived at a recorded instant, and -- now established from the "
            "published protocol -- that the message is the WHOLE book. All "
            "three are real and none of them is a currency guarantee"),
        "not_granted_by_a_token": (
            "the refusal is not relieved by passing a `subscription` argument. "
            "The missing preconditions are properties of the FEED and a caller "
            "cannot supply them"),
        # THE SCOPE OF THE CONCLUSION, KEPT NARROW ON PURPOSE.
        "the_conclusion_is": (
            "OUR M1 PREDICATE CANNOT QUALIFY THIS FEED, because P5 is "
            "unestablished"),
        "the_conclusion_is_NOT": (
            "that the venue has no engineering route. That does not follow, "
            "and I asserted it. The remaining gap is a documentation and "
            "timing question with named ways to close it, not a demonstrated "
            "impossibility"),
        "how_it_could_close": [
            PRECONDITION_STATUS[P5_DOCUMENTED_TIMING]["what_would_establish_it"],
            PRECONDITION_STATUS[P6_RESYNCHRONISATION]["what_would_establish_it"],
        ],
        "bounds_unchanged": {"max_silence_s": MAX_SILENCE_S,
                             "max_snapshot_age_s": MAX_SNAPSHOT_AGE_S,
                             "these_are_chosen_allowances": True},
    }


# ── per-connection, per-market state ────────────────────────────────

_LOCK = threading.Lock()
#: connection epoch -> when it opened. A new epoch on every (re)connect, so a
#: book cannot be carried silently across a drop.
_EPOCH = {"id": 0, "opened_at": None, "alive_at": None, "closes": 0,
          "messages": 0}
#: slug -> {"received_at", "epoch", "slug_on_payload", "levels"}
_LAST: dict = {}


def connection_opened(*, now=None) -> int:
    """A new connection epoch. Every book from an earlier epoch is invalid.

    WHY THE EPOCH IS THE UNIT. The client does not reconnect, so a drop means a
    caller reconnected -- and between the close and the new subscription there is
    a window whose messages nobody received. With no sequence to check, that gap
    is undetectable, so every state from before the drop is discarded rather than
    aged. Discarding a good book costs a refusal; keeping a stale one costs a
    trade on a price that no longer exists.
    """
    at = float(now if now is not None else time.time())
    with _LOCK:
        _EPOCH["id"] += 1
        _EPOCH.update(opened_at=at, alive_at=at, messages=0)
        _LAST.clear()
        return _EPOCH["id"]


def connection_closed(*, now=None) -> dict:
    """The socket dropped. Invalidate everything and say so."""
    at = float(now if now is not None else time.time())
    with _LOCK:
        _EPOCH["closes"] += 1
        dropped = sorted(_LAST)
        _LAST.clear()
        _EPOCH["alive_at"] = None
        return {"closed_at": at, "epoch": _EPOCH["id"],
                "invalidated_markets": dropped,
                "why": ("with no sequence to detect a gap, a book held across a "
                        "drop has an unknown number of missed updates in front "
                        "of it. Every one is discarded")}


def heartbeat(*, now=None) -> None:
    at = float(now if now is not None else time.time())
    with _LOCK:
        _EPOCH["alive_at"] = at


def message_received(slug, *, now=None, levels=None,
                     payload_slug=None) -> dict:
    """Record one market-data message. A heartbeat too: a message proves life.

    `payload_slug` is what the MESSAGE said, kept separately from the slug we
    asked about so P4 is a comparison rather than an assumption.
    """
    at = float(now if now is not None else time.time())
    with _LOCK:
        _EPOCH["alive_at"] = at
        _EPOCH["messages"] += 1
        row = {"received_at": at, "epoch": _EPOCH["id"],
               "slug_asked": str(slug or ""),
               "slug_on_payload": (None if payload_slug is None
                                   else str(payload_slug)),
               "levels": levels}
        _LAST[str(slug or "")] = row
        return dict(row)


def reset() -> None:
    """Tests only."""
    with _LOCK:
        _EPOCH.update(id=0, opened_at=None, alive_at=None, closes=0, messages=0)
        _LAST.clear()


def subscription_state(slug, *, now=None) -> dict:
    """What the subscription can say about ONE market, precondition by
    precondition. Returns a dict shaped for `bettor_venue_currency.evaluate`'s
    `subscription` argument -- and ONLY when every precondition holds.
    """
    at = float(now if now is not None else time.time())
    with _LOCK:
        epoch = dict(_EPOCH)
        row = dict(_LAST.get(str(slug or "")) or {})

    checks = {}
    # P2 · liveness
    alive = epoch.get("alive_at")
    silence = None if alive is None else at - float(alive)
    checks[P2_LIVENESS] = {
        "met": bool(silence is not None and silence <= MAX_SILENCE_S),
        "silence_s": (None if silence is None else round(silence, 3)),
        "limit_s": MAX_SILENCE_S,
        "why": ("no connection has proven itself alive" if silence is None
                else "last life sign %.1f s ago" % silence),
    }
    # P4 · identity, compared rather than assumed
    same_epoch = bool(row) and row.get("epoch") == epoch.get("id")
    payload_slug = row.get("slug_on_payload")
    identity_ok = bool(row) and (payload_slug is None
                                 or payload_slug == str(slug or ""))
    checks[P4_IDENTITY] = {
        "met": bool(identity_ok and same_epoch),
        "slug_asked": str(slug or ""),
        "slug_on_payload": payload_slug,
        "same_connection_epoch": same_epoch,
        "why": ("no message for this market on the current connection"
                if not row or not same_epoch else
                "the payload names this market" if identity_ok else
                "the payload names a DIFFERENT market"),
    }
    # P1, P5 and P6 · properties of the FEED and its documentation, not of this
    # market. P3 is checked above through the connection epoch, because it is
    # now a fact about OUR socket rather than about the venue's stream.
    for name in (P1_REPLACEMENT_AUTHORITY, P5_DOCUMENTED_TIMING,
                 P6_RESYNCHRONISATION):
        st = PRECONDITION_STATUS[name]
        checks[name] = {"met": bool(st["available"]), "why": st["why"],
                        "serves": PRECONDITION_SERVES[name],
                        "this_is_a_property_of_the_feed": True}
    # P3 · CONNECTION CONTINUITY, which this module can actually enforce: the
    # book must have arrived on the epoch we are still on.
    checks[P3_CONNECTION_CONTINUITY] = {
        "met": bool(row and same_epoch),
        "serves": PRECONDITION_SERVES[P3_CONNECTION_CONTINUITY],
        "why": ("this book arrived on the current connection epoch"
                if row and same_epoch else
                "no book for this market on the current epoch; anything from "
                "an earlier epoch was DISCARDED rather than aged"),
        "this_is_a_property_of_the_feed": False,
        "not_the_withdrawn_delta_requirement": (
            "it does not claim the venue's stream was reassembled without "
            "loss; it claims we did not hold a book across a disconnect"),
    }

    unmet = [n for n in PRECONDITIONS if not checks[n]["met"]]
    age = (None if not row else at - float(row["received_at"]))
    out = {
        "slug": str(slug or ""),
        "mechanism_status": M1_STATUS,
        "checks": checks,
        "unmet": unmet,
        "last_message_age_s": (None if age is None else round(age, 3)),
        "connection": {"epoch": epoch.get("id"),
                       "opened_at": epoch.get("opened_at"),
                       "closes_seen": epoch.get("closes"),
                       "messages_this_epoch": epoch.get("messages")},
        "usable_as_a_currency_mechanism": not unmet,
    }
    if unmet:
        out["why"] = (
            "M1 does not establish this book's currency: %s unmet. %s"
            % (", ".join(unmet),
               "P5/P6 are properties of the published protocol, so no "
               "amount of subscribing supplies them. NOTE the shape of the "
               "gap: replacement authority (P1) IS established, so this is a "
               "TIMING refusal and not a doubt about whether the message is "
               "the whole book"
               if set(unmet) & {P1_REPLACEMENT_AUTHORITY,
                                P5_DOCUMENTED_TIMING, P6_RESYNCHRONISATION}
               else "this is a live condition and may pass on a later cycle"))
        # DELIBERATELY NOT SHAPED for `evaluate`: a caller that passes this
        # through gets no mechanism, because there is none.
        return out
    out["alive_at"] = epoch.get("alive_at")
    out["last_update_at"] = row.get("received_at")
    out["why"] = ("every precondition holds: a full-replacement snapshot for "
                  "this instrument, received %.1f s ago on a connection alive "
                  "%.1f s ago" % (age, silence))
    return out


def evidence_for(slug, *, now=None) -> dict:
    """The production reader. Returns `{"subscription": ...}` for
    `bettor_venue_currency.evaluate`, or `{"subscription": None}` with the reason.

    THIS IS THE FUNCTION `book_currency_evidence` CALLS. It returns None today,
    and it returns None because of `MISSING_PRECONDITIONS`, not because nothing
    is wired -- a distinction that matters when somebody asks what to build next.
    """
    state = subscription_state(slug, now=now)
    if not state.get("usable_as_a_currency_mechanism"):
        return {"subscription": None, "state": state,
                "refusal": M1_NOT_AVAILABLE if MISSING_PRECONDITIONS else
                           "M1_PRECONDITIONS_NOT_MET_FOR_THIS_MARKET",
                "missing_from_the_feed": list(MISSING_PRECONDITIONS),
                "why": state.get("why")}
    return {"subscription": {"alive_at": state["alive_at"],
                             "last_update_at": state["last_update_at"],
                             "slug": state["slug"],
                             "established_by": PRECONDITIONS},
            "state": state}


def describe() -> dict:
    d = mechanism_status()
    d["invalidation"] = {
        "on_disconnect": "every market's state is discarded, not aged",
        "why": ("with no sequence, a book held across a drop has an unknown "
                "number of missed updates in front of it"),
        "epoch_is_the_unit": True,
    }
    d["what_this_module_will_not_do"] = [
        "treat a heartbeat as evidence about a book",
        "treat a recently received message as a full book",
        "age a book across a connection drop",
        "accept a caller-supplied subscription as a substitute for a feed "
        "guarantee",
    ]
    return d
