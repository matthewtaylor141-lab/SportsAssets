"""THE MARKET-DATA DESIGN ASSESSMENT — bounded, and not another predicate.

WHY THIS FILE EXISTS. Four times now I have proposed a freshness predicate,
found it unsupported, and replaced it with another one. In order:

    1  the venue's `transactTime` compared against 30 s     -- meaning unresolved
    2  our request/response round trip                      -- transport latency
    3  our own receipt instant                              -- zero by construction
    4  an HTTP validator answering 304                      -- the representation

Each was a real, correctly measured quantity about the wrong thing, and each
replacement felt like progress because the new predicate had not failed YET. A
fifth candidate was queued up behind them: a clean reconnect-and-resubscribe.

    REPLACING ONE ARBITRARY PREDICATE WITH ANOTHER IS NOT CONVERGENCE. So this
    file is not a predicate. It is the four-part assessment: what the venue
    DOCUMENTS, what we can OBSERVE, what we would be ASSUMING, and what remains
    UNCERTAIN -- with each item attributed to exactly one of those four, because
    the whole failure mode has been items migrating from the last column to the
    first without evidence.

HOW TO READ IT. A design is admissible only if its admitting rule rests on
DOCUMENTED guarantees and OBSERVABLE checks. An item in ASSUMPTIONS is a policy
choice that needs the owner's sign-off and must be labelled as one wherever it
appears. An item in UNCERTAINTY is not available to the design at all.

THE ANSWER THIS PRODUCES, stated up front so nothing here reads as advocacy:
there is no admissible design today, the single reason is that the venue
publishes no timing guarantee, and every other part is either documented or
built. That is a better position than four unmet preconditions -- and it is not
a smaller distance to a trade, because the one gap left is the one gap we cannot
close ourselves.
"""

from __future__ import annotations

DOCUMENTED = "DOCUMENTED_BY_THE_VENUE"
OBSERVABLE = "OBSERVABLE_BY_US"
ASSUMPTION = "EXPLICIT_POLICY_ASSUMPTION"
UNCERTAIN = "REMAINING_UNCERTAINTY"
CATEGORIES = (DOCUMENTED, OBSERVABLE, ASSUMPTION, UNCERTAIN)

#: THE ASSESSMENT. Every item names its category, its evidence, and -- for the
#: things that would otherwise drift -- what it is NOT.
ASSESSMENT = (
    # ── documented by the venue ─────────────────────────────────────
    {
        "id": "FULL_REPLACEMENT_SEMANTICS",
        "category": DOCUMENTED,
        "statement": ("SUBSCRIPTION_TYPE_MARKET_DATA delivers the full order "
                      "book, so a message replaces the displayed book without "
                      "reconstructing earlier updates"),
        "evidence": ("the venue's published subscription-types table: "
                     "'Full order book and market stats'. "
                     "docs.polymarket.us/api-reference/websocket/markets, read "
                     "2026-09-27, run 36332797806"),
        "is_not": ("a latency guarantee. 'Full' describes the message's "
                   "CONTENT, not its age"),
    },
    {
        "id": "NO_INCREMENTAL_MESSAGE_EXISTS",
        "category": DOCUMENTED,
        "statement": ("the protocol documents no delta, increment, diff or "
                      "partial-update message at all"),
        "evidence": ("zero matching sentences on the published page for "
                     "delta / incremental / diff / partial update, and zero "
                     "for sequence / ordering / out-of-order / gap / missed "
                     "message"),
        "consequence": ("a sequence number would be load-bearing for a delta "
                        "stream and is not for this one. Requiring gap-free "
                        "delta continuity here was the wrong shape of "
                        "requirement and is withdrawn"),
    },
    {
        "id": "INSTRUMENT_IDENTITY_ON_EVERY_PAYLOAD",
        "category": DOCUMENTED,
        "statement": "every market-data payload carries `marketSlug`",
        "evidence": ("the payload shape in the pinned SDK and in every "
                     "observed REST body"),
        "and_we_compare_it": ("rather than assuming the message is for the "
                              "market we asked about"),
    },
    {
        "id": "REST_CACHE_POLICY",
        "category": DOCUMENTED,
        "statement": ("GET /v1/markets/{slug}/book answers "
                      "`cache-control: public, max-age=30` and serves `Age`, "
                      "`Last-Modified` and 304 on a conditional request"),
        "evidence": "response headers, verbatim, runs 36332797806 / 36333087522",
        "is_not": ("a freshness guarantee. max-age=30 tells caches they MAY "
                   "serve this for 30 s; it is permission, not a promise about "
                   "the data"),
    },

    # ── observable by us ────────────────────────────────────────────
    {
        "id": "CONNECTION_LIVENESS",
        "category": OBSERVABLE,
        "statement": ("whether the socket has proven itself alive inside a "
                      "bound, from Heartbeat messages"),
        "evidence": "bettor_market_stream.last_heartbeat_at",
        "is_not": ("evidence about the book. Silence on a dead socket and a "
                   "quiet market are indistinguishable"),
    },
    {
        "id": "CONNECTION_CONTINUITY",
        "category": OBSERVABLE,
        "statement": ("whether the book we hold arrived on the connection we "
                      "are still on"),
        "evidence": ("connection epochs in bettor_market_stream, bridged into "
                     "bettor_stream_currency by _tell_currency. A drop "
                     "DISCARDS every market's state rather than ageing it"),
        "is_not": ("a claim that the venue's stream was reassembled without "
                   "loss, and not an age"),
    },
    {
        "id": "RECOVERY_AFTER_A_DROP",
        "category": OBSERVABLE,
        "statement": ("reconnect with backoff, resubscribe every known slug, "
                      "discard the previous connection's books, await a new "
                      "authoritative replacement"),
        "evidence": ("RN1XMarketStream._main: backoff loop, epoch increment, "
                     "every slug returned to REQUESTED"),
        "is_not": ("an upstream-age certificate, AND THIS IS THE FIFTH "
                   "CANDIDATE THAT NEARLY BECAME ONE. A clean recovery says we "
                   "are not holding a stale book. It says nothing about how "
                   "current the replacement we then receive is"),
        "was_misreported_as": ("an unavailable VENUE capability, because the "
                               "SDK does not reconnect. Missing SDK "
                               "convenience is not a missing venue guarantee"),
    },
    {
        "id": "HTTP_RESPONSE_AGE",
        "category": OBSERVABLE,
        "statement": ("the origin generation instant of a REST response, from "
                      "`Date` minus `Age` (RFC 9111 §5.1, §6.1)"),
        "evidence": "bettor_venue_currency.read_contract",
        "is_not": ("a market-data age. An origin can generate a fresh "
                   "response over a delayed source, and on this venue it "
                   "demonstrably does -- last-modified was today on a market "
                   "whose book had not moved since February"),
    },
    {
        "id": "OUR_OWN_PROCESSING_DELAY",
        "category": OBSERVABLE,
        "statement": "how long WE took between receipt and decision",
        "evidence": "ext_pinnacle_loop.MAX_OUR_PROCESSING_DELAY_S",
        "is_not": ("upstream freshness. In a read-then-decide loop this is "
                   "near zero by construction, so using it would make every "
                   "book fresh"),
    },

    # ── explicit policy assumptions ─────────────────────────────────
    {
        "id": "THIRTY_SECOND_BOUND",
        "category": ASSUMPTION,
        "statement": ("a book state older than 30 s is not tradeable on this "
                      "strategy"),
        "why_it_is_an_assumption": ("no venue document states it and no "
                                    "measurement derived it. It is the number "
                                    "the lane has always used"),
        "owner_signoff_required": True,
        "and_it_is_currently_moot": ("nothing establishes a book-state age, so "
                                     "the bound is never reached"),
    },
    {
        "id": "FIFTEEN_SECOND_SILENCE_BOUND",
        "category": ASSUMPTION,
        "statement": "a subscription silent for 15 s is not proven alive",
        "why_it_is_an_assumption": "a chosen allowance, labelled as one",
        "owner_signoff_required": True,
    },
    {
        "id": "DISCARD_RATHER_THAN_AGE_ON_A_DROP",
        "category": ASSUMPTION,
        "statement": ("a book carried across a disconnect is discarded rather "
                      "than aged"),
        "why_it_is_an_assumption": ("it is a CHOICE about which error to "
                                    "prefer. Discarding a good book costs a "
                                    "refusal; keeping a stale one costs a "
                                    "trade at a price that no longer exists"),
        "owner_signoff_required": False,
        "why_not": ("it can only refuse more, never admit more, so it cannot "
                    "authorise anything"),
    },

    # ── remaining uncertainty ───────────────────────────────────────
    {
        "id": "NO_PUBLISHED_TIMING_GUARANTEE",
        "category": UNCERTAIN,
        "statement": ("nothing the venue publishes states how current a "
                      "market-data message is: no as-of instant, no latency "
                      "bound, no staleness contract"),
        "evidence": ("zero matching sentences for latency / delay / as-of on "
                     "the published WebSocket page"),
        "this_is_the_single_blocking_item": True,
        "what_would_close_it": ("the venue documenting it. This is the one gap "
                               "no engineering on our side can fill, and "
                               "substituting an observable for it is the "
                               "mistake this file exists to stop"),
    },
    {
        "id": "TRANSACT_TIME_DENOTATION",
        "category": UNCERTAIN,
        "statement": ("what `transactTime` denotes. Last book change, last "
                      "trade, settlement and representation generation all "
                      "remain open"),
        "evidence": ("observed old on expired zero-depth cached markets, which "
                     "every hypothesis predicts"),
        "what_would_close_it": "the venue's contract for the field",
        "what_would_NOT_close_it": (
            "a moving-book experiment on its own. Correlation is evidence "
            "about last-change versus settlement or generation; it is not a "
            "semantic guarantee and must not become one"),
        "my_withdrawn_argument": (
            "'a response stamp cannot precede its own response' -- FALSE. A "
            "stamp can denote representation generation, which precedes "
            "transmission, and a cached representation keeps its original "
            "stamp"),
    },
    {
        "id": "RUNNER_VERSUS_PRODUCTION_PATH",
        "category": UNCERTAIN,
        "statement": ("whether production's responses match the runner's. "
                      "Different egress region and CDN edge, the SDK sends "
                      "Authorization where the probe sent none, and Render may "
                      "add or strip hop headers"),
        "what_would_close_it": ("a gated deployment and its own readback. The "
                               "protocol findings hold regardless; our PATH "
                               "does not inherit them"),
    },
)


def by_category(cat: str) -> tuple:
    return tuple(a for a in ASSESSMENT if a["category"] == cat)


def blocking_items() -> tuple:
    return tuple(a["id"] for a in ASSESSMENT
                 if a.get("this_is_the_single_blocking_item"))


def admissible() -> dict:
    """IS THERE AN ADMISSIBLE DESIGN TODAY? No, and for one reason.

    Admissible means the admitting rule rests only on DOCUMENTED guarantees and
    OBSERVABLE checks. An UNCERTAIN item cannot be in it at all.
    """
    blockers = [a for a in ASSESSMENT
                if a["category"] == UNCERTAIN
                and a.get("this_is_the_single_blocking_item")]
    return {
        "admissible": not blockers,
        "blocking": [a["id"] for a in blockers],
        "why": (
            "an admitting rule may use only what the venue documents and what "
            "we can observe. The one thing it needs -- how current a message "
            "is -- is documented nowhere and observable by nothing we have"),
        "what_is_NOT_the_reason": [
            "replacement authority -- DOCUMENTED",
            "instrument identity -- DOCUMENTED",
            "connection liveness -- OBSERVABLE",
            "connection continuity -- OBSERVABLE",
            "recovery after a drop -- OBSERVABLE and IMPLEMENTED",
        ],
        "and_the_distance_to_a_trade_has_not_shortened": (
            "four unmet preconditions became one, and the one that remains is "
            "the one we cannot close ourselves. That is a clearer position, "
            "not a nearer one"),
    }


def the_five_rejected_predicates() -> tuple:
    """WHAT WAS PROPOSED AND WHY EACH FAILED. Kept so a sixth is recognisable.

    Every one was a real quantity, measured correctly, about the wrong thing.
    """
    return (
        {"predicate": "VENUE_TRANSACT_TIME_UNDER_30S",
         "failed_because": "the field's denotation is unresolved"},
        {"predicate": "OUR_REQUEST_RESPONSE_ROUND_TRIP",
         "failed_because": ("it measures transport latency. A server can "
                            "answer in 20 ms with a snapshot cached minutes "
                            "ago -- and the faster it answers, the stronger "
                            "the false certificate")},
        {"predicate": "OUR_RECEIPT_INSTANT",
         "failed_because": ("near zero by construction in read-then-decide, "
                            "so it makes every book fresh")},
        {"predicate": "AN_HTTP_VALIDATOR_ANSWERING_304",
         "failed_because": ("a 304 affirms the REPRESENTATION. This venue's "
                            "last-modified was today over a book that had not "
                            "moved since February")},
        {"predicate": "A_CLEAN_RECONNECT_AND_RESUBSCRIBE",
         "failed_because": ("it establishes that we are not holding a stale "
                            "book, which is not the same as establishing that "
                            "the new one is current"),
         "note": ("this one was never shipped as a predicate. It is listed "
                  "because it was the next candidate and it is the most "
                  "plausible-looking of the five")},
    )


def describe() -> dict:
    return {
        "categories": {c: [a["id"] for a in by_category(c)] for c in CATEGORIES},
        "admissible": admissible(),
        "rejected_predicates": [p["predicate"]
                                for p in the_five_rejected_predicates()],
        "this_is_not_a_predicate": (
            "it is the assessment. Four times a predicate was proposed, found "
            "unsupported and replaced; the replacement is the pattern this "
            "file exists to break"),
    }
