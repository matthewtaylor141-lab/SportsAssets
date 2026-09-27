"""CAN A MARKET-DATA SUBSCRIPTION ESTABLISH THAT A BOOK IS CURRENT? M1, VERIFIED.

`bettor_venue_currency` names M1 -- a live market-data subscription -- as one of
two mechanisms that can establish a venue book's currency. This module is the
verification of that claim, and the verification does not come out the way the
name suggests.

    A CONNECTION IS NOT A GUARANTEE. A heartbeat proves a socket is open. A
    recently received message proves a message arrived. Neither proves that the
    book we hold is the venue's book, and treating either as though it did would
    be the receipt-instant error again with a new token in front of it.

WHAT M1 WOULD NEED, AND IT IS FOUR THINGS, NOT ONE
--------------------------------------------------

  P1  REPLACEMENT AUTHORITY. Every message must carry the WHOLE book, or we must
      be able to apply deltas to a known-good base. If a message is a partial
      update and we treat it as a replacement, our book is a fiction assembled
      from fragments.
  P2  LIVENESS. The connection must have proven itself alive inside a bound.
      Silence on a dead socket and a quiet market are indistinguishable.
  P3  CONTINUITY. We must be able to prove no message was lost between the last
      state we trust and now. This needs a SEQUENCE, or an equivalent, because a
      gap is invisible by construction: a message that never arrives leaves no
      trace.
  P4  IDENTITY. The message must be for the instrument we are pricing --
      compared on the venue's own market slug, not on a name or a substring.

WHAT THIS VENUE'S FEED ACTUALLY PROVIDES, BY INSPECTION OF THE SHIPPED CLIENT
----------------------------------------------------------------------------

Read from `polymarket_us.websocket` in the installed SDK, which is the contract
this system is bound by whatever any prose says:

  `_MarketDataPayload`   marketSlug, bids, offers, state, stats, transactTime
  `MarketData`           requestId, subscriptionType, marketData
  `Heartbeat`            present as a message type
  `base.connect`         opens the socket, starts one message loop
  `base._message_loop`   `except websockets.ConnectionClosed: self._emit("close")`
                         -- and the loop RETURNS. There is no reconnect, no
                         resubscribe and no resynchronisation in the client.

So, precondition by precondition:

  P1  NOT ESTABLISHED. `MarketData`'s docstring says "full order book", and a
      docstring is not a protocol guarantee. `obs/streamstate.DepthAuthority`
      already refuses this exact inference in this repository's own words: a
      dataclass with a `bids` list establishes that a list arrives, not that the
      list is a full replacement. Nothing in the payload distinguishes a snapshot
      from an increment -- there is no `type`, no `isSnapshot`, no `action`.
  P2  AVAILABLE. A heartbeat message type exists and
      `bettor_market_stream` already records `last_heartbeat_at`.
  P3  NOT AVAILABLE. There is NO sequence number, no message id, no continuity
      field of any kind in `_MarketDataPayload`. A dropped message therefore
      cannot be detected, only assumed absent. `transactTime` cannot substitute:
      what it denotes is unresolved, and even a last-change stamp would not
      reveal a missing message between two changes.
  P4  AVAILABLE. `marketSlug` is on the payload and is the venue's own
      identifier.

THE CONCLUSION, AND IT IS A REFUSAL
-----------------------------------

M1 CANNOT ESTABLISH CURRENCY ON THIS FEED. Two of its four preconditions are
unavailable from the protocol as shipped -- one unestablished (P1) and one
missing outright (P3). A subscription here can tell us a socket is open and that
a message for the right instrument arrived recently. That is genuinely useful for
liquidity observation and for alerting. It is not a currency guarantee, and this
module returns `M1_NOT_AVAILABLE` with the missing guarantees named rather than
granting freshness through a token called "subscription".

WHY THE MACHINERY IS BUILT ANYWAY
---------------------------------

Three reasons, and none of them is optimism:

  * the verdict has to be COMPUTED from the live state rather than hard-coded,
    so that if the venue publishes a sequence field or confirms replacement
    semantics, the mechanism becomes available by measurement and not by someone
    deciding it has;
  * disconnect invalidation is correct regardless. A book carried across a
    connection drop is a book with an undetectable gap in front of it, and this
    module invalidates it whether or not M1 is ever load-bearing;
  * the same state answers "how stale is our liquidity picture", which the depth
    and sizing evidence legitimately wants.

NOTHING HERE WIDENS A BOUND. The single number it can produce, when every
precondition is met, is the age of a full snapshot for the right instrument on a
connection with proven continuity -- which is a stricter statement than any this
lane has made.
"""

from __future__ import annotations

import threading
import time

# ── the four preconditions, named so a refusal can point at one ──────

P1_REPLACEMENT_AUTHORITY = "P1_FULL_REPLACEMENT_AUTHORITY"
P2_LIVENESS = "P2_CONNECTION_LIVENESS"
P3_CONTINUITY = "P3_GAP_FREE_CONTINUITY"
P4_IDENTITY = "P4_INSTRUMENT_IDENTITY"
PRECONDITIONS = (P1_REPLACEMENT_AUTHORITY, P2_LIVENESS, P3_CONTINUITY,
                 P4_IDENTITY)

M1_AVAILABLE = "M1_AVAILABLE"
M1_NOT_AVAILABLE = "M1_NOT_AVAILABLE_ON_THIS_FEED"

#: ── WHAT THE SHIPPED CLIENT PROVIDES. Determined by inspection, dated, and
#: carrying the artefact each conclusion rests on so a reader can check it.
FEED_CONTRACT = {
    "determined_on": "2026-09-27",
    "method": ("inspection of the installed polymarket_us.websocket module -- "
               "types.py for the payload shape, base.py for the lifecycle. The "
               "shipped client is the contract this system is bound by, "
               "whatever any prose says"),
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
    "full_book_claim_rests_on": (
        "MarketData's docstring, which says 'full order book'. A docstring is "
        "not a protocol guarantee, and obs/streamstate.DepthAuthority already "
        "refuses this inference in this repository's own words"),
}

PRECONDITION_STATUS = {
    P1_REPLACEMENT_AUTHORITY: {
        "available": False,
        "why": ("nothing in the payload distinguishes a snapshot from an "
                "increment -- no type, no isSnapshot, no action. The 'full "
                "order book' claim is a docstring"),
        "what_would_establish_it": ("the venue documenting the message as a full "
                                    "replacement, or a marker on the message"),
    },
    P2_LIVENESS: {
        "available": True,
        "why": ("a Heartbeat message type exists and bettor_market_stream "
                "already records last_heartbeat_at"),
    },
    P3_CONTINUITY: {
        "available": False,
        "why": ("there is no sequence number, message id or continuity field of "
                "any kind. A dropped message leaves no trace, so a gap cannot "
                "be detected -- only assumed absent, which is the assumption "
                "this whole gate exists to refuse"),
        "transact_time_cannot_substitute": (
            "what it denotes is unresolved, and even a last-change stamp would "
            "not reveal a message missing BETWEEN two changes"),
        "what_would_establish_it": "a per-market sequence the venue publishes",
    },
    P4_IDENTITY: {
        "available": True,
        "why": "marketSlug is on the payload and is the venue's own identifier",
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


def mechanism_status() -> dict:
    """Whether M1 can establish anything on this feed, and what is missing."""
    return {
        "mechanism": "M1_LIVE_MARKET_DATA_SUBSCRIPTION",
        "status": M1_STATUS,
        "preconditions": {n: dict(PRECONDITION_STATUS[n]) for n in PRECONDITIONS},
        "missing": list(MISSING_PRECONDITIONS),
        "feed_contract": FEED_CONTRACT,
        "what_a_subscription_here_does_prove": (
            "that a socket is open, and that a message for a named instrument "
            "arrived at a recorded instant. Both are real and neither is a "
            "currency guarantee"),
        "not_granted_by_a_token": (
            "the refusal is not relieved by passing a `subscription` argument. "
            "The preconditions are properties of the FEED and a caller cannot "
            "supply them"),
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
    # P1 and P3 · properties of the feed, not of this market
    for name in (P1_REPLACEMENT_AUTHORITY, P3_CONTINUITY):
        st = PRECONDITION_STATUS[name]
        checks[name] = {"met": bool(st["available"]), "why": st["why"],
                        "this_is_a_property_of_the_feed": True}

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
               "P1 and P3 are missing from the feed itself, so no amount of "
               "subscribing supplies them"
               if set(unmet) & {P1_REPLACEMENT_AUTHORITY, P3_CONTINUITY}
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
