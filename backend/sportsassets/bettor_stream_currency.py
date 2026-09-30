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

WHAT IS KNOWN ABOUT `transactTime` -- AND MY "DISCRIMINATOR" IS WITHDRAWN
------------------------------------------------------------------------

I wrote that the question was settled in one direction by this argument:

    "A response stamp cannot precede its own response, so transactTime
     materially before the response Date CONTRADICTS the response-stamp
     reading."

THAT PREMISE IS FALSE and the conclusion built on it is withdrawn. Two ways it
fails, either of which is enough:

  * A TIMESTAMP CAN DESCRIBE REPRESENTATION GENERATION, which happens BEFORE
    transmission. A stamp earlier than the `Date` on the wire is exactly what a
    generation instant looks like. Nothing is contradicted.
  * A CACHED REPRESENTATION RETAINS ITS OLD TIMESTAMP. The observed responses
    carried `cf-cache-status: EXPIRED` then `HIT` and `cache-control: max-age=30`,
    so a served representation may well be one generated earlier and stored --
    with its original stamp intact.

So a 219-day-old value is fully consistent with `transactTime` being a
REPRESENTATION-GENERATION instant of a long-cached representation. My argument
excluded a hypothesis it had no power to exclude, which is the same error as the
resampling inference I withdrew earlier wearing a more rigorous-looking sleeve.

WHAT THE OBSERVATIONS DO ESTABLISH, and it is narrow:

    THE VALUE WAS OLD IN THE RETURNED REPRESENTATION. That is all. It is a fact
    about what came back, not about what the field denotes.

FOUR HYPOTHESES REMAIN OPEN AND NONE IS PREFERRED:

    H1  last book change
    H2  last trade
    H3  settlement / market close
    H4  representation generation

On an expired, zero-depth, cached market H1-H4 all predict an old value, which is
why these observations cannot separate them. They are recorded as observations in
`TRANSACT_TIME["observed"]` and the interpretations are kept in a separate key so
the two cannot be read as one.

AND A MOVING-BOOK EXPERIMENT WOULD NOT CLOSE IT EITHER, on its own.
Correlation between a moving book and a moving stamp is evidence about H1 versus
H3/H4; it is not a semantic guarantee, and it must not become the third asserted
one. What would close it is the venue's contract. Until then the field's meaning
is UNRESOLVED and nothing is gated on it.

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

#: WHAT `transactTime` DENOTES. UNRESOLVED -- and structured so an OBSERVATION
#: can never be read as an INTERPRETATION.
#:
#: The previous version of this constant carried an "ESTABLISHED" key asserting
#: the field was a market-data instant. That rested on "a response stamp cannot
#: precede its own response", which is FALSE: a stamp can denote representation
#: GENERATION, which precedes transmission, and a cached representation keeps its
#: original stamp. The claim is withdrawn and the withdrawal is recorded here
#: rather than edited away.
TRANSACT_TIME = {
    "denotation": "UNRESOLVED",
    "nothing_is_gated_on_it": True,

    # ── OBSERVATIONS. Facts about what came back. No inference. ──────
    "observed": {
        "determined_on": "2026-09-27",
        "runs": ["36332797806", "36333087522"],
        "markets": 2,
        "market_state": "MARKET_STATE_EXPIRED",
        "book_levels": "0 bids / 0 offers on every read",
        "cache_status": "cf-cache-status: EXPIRED on the 200, HIT on the 304",
        "cache_control": "public, max-age=30",
        "transact_time_value": "2026-02-20T03:07:30.947946180Z",
        "response_date": "Sun, 27 Sep 2026 16:24:27 GMT",
        "date_minus_transact_time_s": 18969408.2,
        "across_an_8s_gap": ("date advanced 8.0 s; transactTime advanced "
                             "0.0 s; last-modified advanced 0.0 s"),
    },

    # ── WHAT THE OBSERVATIONS SUPPORT. Deliberately almost nothing. ──
    "what_this_establishes": (
        "the value was OLD IN THE RETURNED REPRESENTATION. That is a fact about "
        "what came back, not about what the field denotes"),

    # ── THE OPEN HYPOTHESES. None preferred. ────────────────────────
    "hypotheses": {
        "H1_LAST_BOOK_CHANGE": "the instant the book last changed",
        "H2_LAST_TRADE": "the instant of the last execution",
        "H3_SETTLEMENT_OR_CLOSE": "the instant the market closed or settled",
        "H4_REPRESENTATION_GENERATION": (
            "the instant this representation was generated, which PRECEDES "
            "transmission and survives caching unchanged"),
    },
    "why_the_observations_cannot_separate_them": (
        "on an expired, zero-depth, cached market H1 through H4 all predict an "
        "old value. The reads have no discriminating power at all"),

    # ── THE WITHDRAWN ARGUMENT, KEPT SO IT CANNOT RECUR. ────────────
    "withdrawn_argument": {
        "what_I_claimed": (
            "a response stamp cannot precede its own response, so a 219-day lag "
            "CONTRADICTS the response-stamp reading -- one observation suffices "
            "in that direction"),
        "why_it_is_false": (
            "a timestamp can describe representation GENERATION, which happens "
            "before transmission, so a stamp earlier than the wire `Date` is "
            "exactly what a generation instant looks like. And a cached "
            "representation retains its original timestamp -- these very "
            "responses were served from a CDN under max-age=30 with "
            "cache-status EXPIRED then HIT"),
        "so": ("H4 was never excluded. The argument ruled out a hypothesis it "
               "had no power to rule out"),
        "the_pattern": (
            "this is the resampling inference I withdrew earlier, wearing a "
            "more rigorous-looking sleeve: a conclusion whose premise sounded "
            "like a logical necessity and was a guess about the venue"),
    },

    # ── WHAT WOULD ACTUALLY RESOLVE IT, AND WHAT WOULD NOT. ─────────
    "what_would_resolve_it": (
        "the venue's own contract for the field. Nothing on the pages read "
        "states it"),
    "what_would_NOT_resolve_it": (
        "a moving-book experiment on its own. Correlation between a moving book "
        "and a moving stamp is EVIDENCE about H1 against H3/H4 and is not a "
        "semantic guarantee. It must not become the third asserted certificate "
        "after transport latency and our own receipt instant"),
    "why_more_unchanged_samples_cannot_help": (
        "a quiet book, a delayed feed and a cached representation all predict "
        "the same unchanged value"),

    # ── WHAT TURNS ON IT, stated without presuming the answer. ──────
    "what_turns_on_it": (
        "under H1 an old value means a quiet money line, and a 30 s bound on it "
        "refuses every quiet market while calling it freshness -- the bound "
        "applied to the wrong quantity, which is a separate repair and NOT a "
        "loosening. Under H4 it is a property of the representation and bounds "
        "the response, like Date-Age. Under H2/H3 it is neither. The repairs "
        "differ, so the field cannot be gated on until the denotation is known"),
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
            "guarantee. That is the only thing that ESTABLISHES P5, because P5 "
            "is a property of the published protocol"),
        # ── AND THE CORRECTION TO WHAT I SAID NEXT ───────────────────
        #
        # This entry used to offer a second route: "or a read of a demonstrably
        # MOVING book that separates last-change from now". I then reported that
        # as the concrete next step for M1, which overstated it, and a test of
        # mine asserted the phrase "MOVING book" appeared in this string -- a
        # test that proves a sentence exists and no timing property whatever.
        #
        # A MOVING BOOK CAN STILL BE DELAYED. Observing prices and timestamps
        # change tells you the feed is not frozen. It does not bound how old any
        # message was when it arrived, because a delayed feed moves too: a
        # sixty-second-late stream of a moving book shows exactly the same
        # movement as a current one. Movement separates LIVE from STALE-FROZEN.
        # It does not separate CURRENT from LATE, and P5 is about the second.
        "movement_does_not_establish_it": (
            "a delayed feed of a moving book shows the same movement as a "
            "current one, so movement distinguishes live from frozen and not "
            "current from late. P5 is about the second"),
        "what_a_movement_experiment_could_distinguish": (
            "whether transactTime advances with observed book changes, and by "
            "how much it lags OUR receipt of them -- which is evidence about "
            "the field's denotation and about OUR path, both within the scope "
            "of an independent reference clock. It leaves the UPSTREAM interval "
            "between the matching engine and the venue's egress unmeasured, and "
            "that interval is what P5 asks about"),
        "so_it_is_evidence_not_a_promotion": (
            "the result would be recorded as an empirical finding inside its "
            "stated scope. P5 stays unavailable on it: promoting on movement "
            "alone would be a false certificate of exactly the kind this "
            "module's history is a list of"),
        "this_is_the_binding_precondition": True,
        "and_now_the_ONLY_one": (
            "with P6 corrected, P5 is the single unmet precondition. That is a "
            "cleaner and more useful statement than the four-way refusal I "
            "started with: the one thing missing is a guarantee only the VENUE "
            "can give, and no amount of engineering on our side manufactures "
            "it. Everything that was ours to build is built"),
        "what_we_must_NOT_do_about_it": (
            "substitute another observable. Transport latency, our receipt "
            "instant, an HTTP validator and a clean reconnect have each, at "
            "some point in this file's history, been one step from standing in "
            "for it"),
    },
    P6_RESYNCHRONISATION: {
        "available": True,
        "serves": Q2_SUFFICIENTLY_CURRENT,
        "corrected_on": "2026-09-27",
        "previously_said": (
            "NOT ESTABLISHED, on the grounds that 'the protocol documents no "
            "resubscribe-and-resnapshot procedure and the client implements "
            "none'. The second half was wrong and the first half does not "
            "matter -- which is two errors, not one"),
        "why": (
            "RESYNCHRONISATION ON A FULL-REPLACEMENT FEED IS: discard, "
            "reconnect, resubscribe, await the next authoritative replacement. "
            "There is nothing to reconstruct, so no venue procedure is needed "
            "and none being documented is not a missing guarantee. And it is "
            "IMPLEMENTED: RN1XMarketStream._main reconnects with backoff, "
            "increments its epoch, returns every known slug to REQUESTED so it "
            "is resubscribed, and bettor_market_stream._tell_currency drives "
            "this module's own epoch from the same events"),
        "what_the_SDK_does_not_do_and_why_that_is_not_a_venue_gap": (
            "polymarket_us.websocket.base._message_loop emits 'close' and "
            "RETURNS -- it does not reconnect. That is MISSING SDK CONVENIENCE, "
            "not an unavailable venue capability, and I had reported it as the "
            "latter. Our wrapper supplies it"),
        "and_recovery_IS_NOT_AN_AGE": (
            "a successful reconnect and resubscribe establishes CONNECTION "
            "CONTINUITY -- that we are not holding a book across a drop. It "
            "says nothing about how current the replacement we then receive "
            "is. Treating a clean recovery as an upstream-age certificate "
            "would be the same error as treating a fast response or a 304 as "
            "one, and P5 still refuses every admission"),
        "the_implementation_is_verified_in": (
            "test_the_recovery_is_implemented_not_documented_away.py, driving "
            "the production bridge rather than this module's API directly"),
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
        # DERIVED FROM WHAT IS ACTUALLY UNMET, not from a hard-coded pair. P6
        # left the unmet set and this list followed it automatically -- a fixed
        # list would have gone on naming a closed gap.
        "how_it_could_close": [
            PRECONDITION_STATUS[n].get("what_would_establish_it")
            for n in MISSING_PRECONDITIONS
            if PRECONDITION_STATUS[n].get("what_would_establish_it")
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

    It also requires the decision process's own subscription
    (`bettor_market_subscription`) to hold this market as CURRENT, and it
    carries that readiness and its named M1 reason on every answer.
    """
    state = subscription_state(slug, now=now)
    # ── THE SUBSCRIPTION'S OWN READINESS FOR THIS MARKET ─────────────
    #
    # `bettor_market_subscription` is the decision process's subscription:
    # it holds an explicit per-market state (NOT_SUBSCRIBED ... CURRENT ...
    # REFUSED_BY_VENUE) with a named reason. It is CARRIED on every answer,
    # so a refusal says which part of M1 was missing -- a venue refusal or an
    # entitlement denial is named here rather than folded into "no mechanism".
    #
    # AND IT GATES: this market must be CURRENT there IN ADDITION to every
    # precondition below. The two are fed by the same frames, so requiring
    # both can only refuse more -- and requiring it ALWAYS, not only when a
    # subscription happens to be installed, means M1 has exactly one source:
    # the decision process's own supervised subscription. A stream some other
    # caller runs in the same process feeds this module's epoch bookkeeping
    # but can never, on its own, supply the mechanism. With nothing installed
    # the readiness is NOT_SUBSCRIBED and the answer is today's refusal.
    readiness = None
    try:
        from . import bettor_market_subscription as _msub
        readiness = _msub.readiness(slug, now=now)
        sub_refusal = _msub.m1_refusal_name(readiness)
    except Exception as exc:  # noqa: BLE001 -- unknown is NOT current
        readiness = {"state": "UNKNOWN", "why": type(exc).__name__}
        sub_refusal = "M1_SUBSCRIPTION_READINESS_UNREADABLE"
    if sub_refusal is None and MISSING_PRECONDITIONS:
        sub_refusal = "M1_FEED_TIMING_NOT_DOCUMENTED_P5"
    if not state.get("usable_as_a_currency_mechanism"):
        return {"subscription": None, "state": state,
                "refusal": M1_NOT_AVAILABLE if MISSING_PRECONDITIONS else
                           "M1_PRECONDITIONS_NOT_MET_FOR_THIS_MARKET",
                "missing_from_the_feed": list(MISSING_PRECONDITIONS),
                "readiness": readiness,
                "subscription_refusal": sub_refusal,
                "why": state.get("why")}
    if (readiness or {}).get("state") != "CURRENT":
        return {"subscription": None, "state": state,
                "refusal": "M1_SUBSCRIPTION_NOT_CURRENT",
                "missing_from_the_feed": list(MISSING_PRECONDITIONS),
                "readiness": readiness,
                "subscription_refusal": sub_refusal,
                "why": ("every precondition holds in this module, but the "
                        "decision process's subscription does not hold this "
                        "market as CURRENT: %s (%s)"
                        % ((readiness or {}).get("state"),
                           (readiness or {}).get("reason")))}
    return {"subscription": {"alive_at": state["alive_at"],
                             "last_update_at": state["last_update_at"],
                             "slug": state["slug"],
                             "established_by": PRECONDITIONS},
            "state": state, "readiness": readiness}


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
