"""P5_LIVE_STREAM_BOOK_V1: A VERSIONED LIVE BOOK-CURRENTNESS RULE (PURE).

WHAT THIS IS. The strongest book-currentness rule the evidence can DEFEND,
written down as one canonical, hashed document (`DOCUMENT`) and implemented
as one pure function (`evaluate`) over the evidence a RESIDENT institutional
stream book exposes (`institutional_stream.current(symbol)`).

WHAT IT IS NOT.
  * It is not a venue timing guarantee. The venue documentation reviewed on
    2026-10-03 (research/p5_live_book_currency_review.md, verdict B) states
    no change-to-delivery bound, no heartbeat interval, no debounce interval
    and no sequence number, and defines `transact_time` only as "Server
    timestamp of update". The rule therefore splits its evidence three ways,
    and says so in its own text:
      venue_guaranteed  only what the cited pages state (quoted, with URLs);
      locally_bounded   what OUR receipt clock / connection-epoch logic bounds;
      not_established   what neither bounds (named, never assumed away).
  * It is not approved. Approval is an OWNER decision recorded on
    `live_rule_artifacts` (migration 204) against THIS document's sha256; the
    actual lane admits the rule only while that row is APPROVED with an owner
    record AND its stored sha256 equals `SHA256` here
    (`live_rule_artifacts.approved_live_book_rules`). The code constant
    `actual_admission.APPROVED_LIVE_BOOK_RULES` stays EMPTY.
  * It changes no paper behaviour. The paper benchmark records the verdict as
    evidence in `admission_facts.book.book_currency`; the paper decision keeps
    its own BOOK_CURRENCY label and its REST price source.

FAIL CLOSED. Every component passes only on an explicit positive fact. The
verdict is the most severe failing class, in the order
REFUSED > GAP > NOT_ESTABLISHED > STALE; ESTABLISHED only when every component
passes.

THE HASH. `SHA256` is the SHA-256 of `canonical_json(DOCUMENT)` (sorted keys,
compact separators, ASCII). Migration 204 stores that exact text; a test pins
the migration literal against this module. Changing the rule (a bound, a
requirement, a citation) is a NEW VERSION with a new artifact row -- never an
edit of V1.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable

RULE_ID = "P5_LIVE_STREAM_BOOK_V1"
VERSION = "1"

# ── verdicts ─────────────────────────────────────────────────────────
ESTABLISHED = "ESTABLISHED"
STALE = "STALE"
GAP = "GAP"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
REFUSED = "REFUSED"
VERDICTS = (ESTABLISHED, STALE, GAP, NOT_ESTABLISHED, REFUSED)
#: Most severe first: the verdict is the first class any component fails with.
PRECEDENCE = (REFUSED, GAP, NOT_ESTABLISHED, STALE)

# ── bounds (part of the versioned text) ──────────────────────────────
#: Our receipt instant of the symbol's last complete book, at evaluation.
MAX_RECEIPT_AGE_S = 2.0
#: |our receipt instant - venue transact_time| of that book.
MAX_VENUE_RECEIPT_SKEW_S = 2.0
#: The actual lane submits only this soon after the verdict was evaluated.
MAX_VERDICT_AGE_AT_SUBMIT_S = 2.0
#: A receipt instant this far in OUR future is a clock fault, not a book.
RECEIPT_FUTURE_TOLERANCE_S = 0.25
#: Venue instrument states that are explicitly open for trading.
TRADABLE_INSTRUMENT_STATES = ("INSTRUMENT_STATE_OPEN",)

SEQUENCE_NOT_PROVIDED = "SEQUENCE_NOT_PROVIDED_BY_VENUE"
SUBSCRIPTION_RUNNING = "RUNNING"

# ── reasons ──────────────────────────────────────────────────────────
R_IDENTITY_UNMAPPED = "IDENTITY_MAPPING_NOT_ESTABLISHED"
R_IDENTITY_MISMATCH = "IDENTITY_MAPPING_NOT_EXACT_FOR_THIS_BOOK"
R_NO_STREAM_READ = "NO_RESIDENT_STREAM_READ"
R_STREAM_NOT_RUNNING = "STREAM_NOT_RUNNING"
R_STREAM_REFUSED = "STREAM_REFUSED_BY_VENUE"
R_NO_EPOCH = "NO_LIVE_CONNECTION_EPOCH"
R_EPOCH_LOST = "CONNECTION_EPOCH_LOST_BOOK_DISCARDED"
R_GAP_RECONNECT = "GAP_NO_COMPLETE_BOOK_ON_THIS_EPOCH_SINCE_RECONNECT"
R_SNAPSHOT_PENDING = "NO_COMPLETE_BOOK_ON_THIS_EPOCH_YET"
R_NO_VENUE_TS = "VENUE_TRANSACT_TIME_ABSENT"
R_TS_BACKWARDS = "GAP_VENUE_TRANSACT_TIME_WENT_BACKWARDS_IN_EPOCH"
R_NO_RECEIPT = "LOCAL_RECEIPT_INSTANT_ABSENT"
R_RECEIPT_FUTURE = "LOCAL_RECEIPT_INSTANT_IN_THE_FUTURE"
R_RECEIPT_OLD = "LOCAL_RECEIPT_AGE_ABOVE_BOUND"
R_SKEW_LATE = "VENUE_TS_OLDER_THAN_RECEIPT_BEYOND_BOUND"
R_SKEW_AHEAD = "VENUE_TS_AHEAD_OF_RECEIPT_BEYOND_BOUND"
R_STATE_UNKNOWN = "MARKET_STATE_UNKNOWN"
R_NOT_OPEN = "MARKET_NOT_OPEN"
R_STREAM_NOT_CURRENT = "RESIDENT_BOOK_REFUSED_BY_STREAM"
R_PRICE_NOT_STATED = "PRICED_BOOK_SOURCE_NOT_STATED"
R_PRICE_OTHER_BOOK = "PRICED_BOOK_IS_NOT_THE_EVALUATED_STREAM_BOOK"
R_VERDICT_STALE = "LIVE_BOOK_VERDICT_STALE_AT_ACTUAL_SUBMIT"

#: How the stream's own refusal codes (institutional_stream.R_*) classify.
#: An unknown code is NOT_ESTABLISHED.
STREAM_REFUSAL_CLASS = {
    "INSTITUTIONAL_STREAM_NOT_RUNNING_IN_THIS_PROCESS": NOT_ESTABLISHED,
    "VENUE_REFUSED_THE_STREAM_CREDENTIAL": REFUSED,
    "RECONNECT_BOUND_EXHAUSTED": NOT_ESTABLISHED,
    "SYMBOL_NOT_REQUESTED": NOT_ESTABLISHED,
    "VENUE_REFUSED_THIS_SYMBOL": REFUSED,
    "INSTRUMENT_SCALES_UNKNOWN": NOT_ESTABLISHED,
    "NO_CONNECTION_OPEN": NOT_ESTABLISHED,
    "GAP_CONNECTION_LOST_AWAITING_FRESH_SNAPSHOT": GAP,
    "GAP_VENUE_CLOCK_WENT_BACKWARDS_AWAITING_FRESH_SNAPSHOT": GAP,
    "AWAITING_FIRST_SNAPSHOT_ON_THIS_CONNECTION": NOT_ESTABLISHED,
    "UPDATE_CARRIES_NO_VENUE_TIMESTAMP": NOT_ESTABLISHED,
    "CONNECTION_SILENT_PAST_THE_LIVENESS_BOUND": STALE,
    "SNAPSHOT_OLDER_THAN_THE_BOUND": STALE,
    "VENUE_REPORTS_BOOK_HIDDEN": REFUSED,
    "MARKET_NOT_OPEN": REFUSED,
    "MARKET_STATE_UNKNOWN": NOT_ESTABLISHED,
    "BOOK_CROSSED": REFUSED,
    # red team stream guard: connected is not current (complete snapshot,
    # no gap, current book) -- the book is not established
    "STREAM_CURRENCY_GATE_REFUSED": NOT_ESTABLISHED,
}
_GAP_CONNECTION = "GAP_CONNECTION_LOST_AWAITING_FRESH_SNAPSHOT"
_GAP_CLOCK = "GAP_VENUE_CLOCK_WENT_BACKWARDS_AWAITING_FRESH_SNAPSHOT"
_STREAM_RUNNING = ("IDLE_NO_SYMBOLS_REQUESTED", "CONNECTING", "CONNECTED",
                   "RECONNECTING")
_STREAM_REFUSED = ("REFUSED_BY_VENUE", "CREDENTIAL_REFUSED_BY_IDENTITY_GUARD")

_D = "https://docs.polymarket.us/"
_REVIEW = "research/p5_live_book_currency_review.md"

#: THE EVIDENCE SPLIT. Quotes are verbatim from the pages as recorded in the
#: 2026-10-03 review (read 11:05-11:12 UTC); nothing here is paraphrased into
#: a guarantee the page does not make.
VENUE_GUARANTEED = [
    {"id": "VG1_SNAPSHOT_ON_SUBSCRIBE_AND_RECONNECT",
     "statement": ("After a market-data subscribe, and after a reconnect, the "
                   "stream sends a snapshot of the book before further "
                   "updates."),
     "quotes": [
         {"url": _D + "streaming-endpoints/streaming-best-practices",
          "quote": ("Market data | Snapshot, then updates (`snapshot_only: "
                    "false`). | Reconnect. Take the new snapshot.")},
         {"url": _D + "trader-guide/streaming-apis",
          "quote": "Order stream and market data send a snapshot, then updates."}]},
    {"id": "VG2_EACH_MESSAGE_IS_A_COMPLETE_BOOK",
     "statement": ("Each market-data message is a complete book (full "
                   "replacement, not a delta); the documented MarketDataUpdate "
                   "carries no delta or action field."),
     "quotes": [
         {"url": _D + "trader-guide/market-data",
          "quote": ("snapshot-style updates (each message is complete); treat "
                    "each message as a full update unless documentation "
                    "specifies delta semantics")},
         {"url": _D + "trader-guide/market-data",
          "quote": "as snapshots (full book, not deltas)"}],
     "caveat": ("Other pages say 'Snapshot, then updates' without defining "
                "'updates'; no page documents delta semantics. The rule treats "
                "every message as a full replacement, as the stream does.")},
    {"id": "VG3_TRANSACT_TIME_IS_A_SERVER_TIMESTAMP",
     "statement": ("Every MarketDataUpdate may carry transact_time, defined "
                   "only as a server timestamp of the update."),
     "quotes": [
         {"url": _D + "streaming-endpoints/market-data-stream",
          "quote": "Server timestamp of update"},
         {"url": _D + "streaming-endpoints/proto-reference",
          "quote": "Server timestamp of update"}],
     "caveat": ("Which server and which event (matching-engine book change, "
                "publisher generation, gateway send) is NOT stated.")},
    {"id": "VG4_CONTINUOUS_UPDATES_WHILE_SUBSCRIBED",
     "statement": ("With snapshot_only false (the default) the stream keeps "
                   "sending updates after the snapshot."),
     "quotes": [
         {"url": _D + "streaming-endpoints/market-data-stream",
          "quote": ("If `False` (default), receive continuous updates.")}]},
    {"id": "VG5_HEARTBEATS_ARE_CONNECTION_KEEPALIVES",
     "statement": ("Heartbeats confirm the CONNECTION is active; their "
                   "absence means the connection may be stale."),
     "quotes": [
         {"url": _D + "streaming-endpoints/market-data-stream",
          "quote": "Keep-alive messages to confirm connection is active."},
         {"url": _D + "streaming-endpoints/market-data-stream",
          "quote": ("If you stop receiving heartbeats, the connection may be "
                    "stale. Consider reconnecting.")}],
     "caveat": "No interval, no timestamp, no per-market meaning is stated."},
    {"id": "VG6_NO_SEQUENCE_NUMBER",
     "statement": ("The documented MarketDataUpdate fields are symbol, bids, "
                   "offers, state, stats, transact_time, book_hidden, "
                   "price_scale, quantity_scale: there is no sequence field. "
                   "Recorded as " + SEQUENCE_NOT_PROVIDED + "."),
     "basis": "DOCUMENTED_FIELD_LIST (the absence of a sequence field)",
     "documented_fields": ["symbol", "bids", "offers", "state", "stats",
                           "transact_time", "book_hidden", "price_scale",
                           "quantity_scale"],
     "sources": [_D + "streaming-endpoints/market-data-stream",
                 _D + "streaming-endpoints/proto-reference"],
     "quotes": []},
]

LOCALLY_BOUNDED = [
    {"id": "LB1_CONNECTION_EPOCH",
     "statement": ("The book was received on the CURRENT connection epoch, "
                   "which is open. Any disconnect discards every held book "
                   "(GAP) until a complete book arrives on a LATER epoch; a "
                   "book from an earlier epoch is never aged into currency."),
     "implemented_by": "institutional_stream.ResidentBooks (on_connected / "
                       "on_disconnected / current)"},
    {"id": "LB2_COMPLETE_BOOK_AFTER_CONNECT_RECONNECT_GAP",
     "statement": ("A complete book for this symbol has been received on this "
                   "epoch after the most recent connect, reconnect or gap, "
                   "and no gap is open for it."),
     "implemented_by": "snapshot.on_current_connection and gap is null"},
    {"id": "LB3_RECEIPT_AGE",
     "statement": ("now - (our receipt instant of that book) <= %.1f s by the "
                   "deciding host's clock. On a quiet book this is the ONLY "
                   "staleness bound (no heartbeat interval is documented), so "
                   "a book that has not produced a message for this symbol "
                   "within %.1f s is STALE."
                   % (MAX_RECEIPT_AGE_S, MAX_RECEIPT_AGE_S))},
    {"id": "LB4_VENUE_TS_PRESENT_MONOTONIC_AND_SKEW_BOUNDED",
     "statement": ("transact_time is present; it never went backwards for "
                   "this symbol within the epoch (a regression is a GAP until "
                   "an update at or past the high-water mark); and "
                   "|receipt - transact_time| <= %.1f s. This bounds the SUM "
                   "of server-to-receipt delay and host-clock offset; it does "
                   "not bound either alone." % MAX_VENUE_RECEIPT_SKEW_S)},
    {"id": "LB5_MARKET_OPEN",
     "statement": ("The instrument state carried by the stream (or refdata) "
                   "is one of %s; the stream's own checks (book not hidden, "
                   "not crossed, scales known) pass."
                   % ", ".join(TRADABLE_INSTRUMENT_STATES))},
    {"id": "LB6_EXACT_CONTRACT_IDENTITY",
     "statement": ("An identity mapping (retail slug <-> institutional symbol) "
                   "returned EXACT for this slug, and the evaluated book is "
                   "that symbol's. No mapping, or any other answer, never "
                   "passes.")},
    {"id": "LB7_PRICED_FROM_THIS_BOOK",
     "statement": ("The decision's executable price and depth were read from "
                   "THIS stream observation (same epoch, same receipt "
                   "instant). A price from another book (e.g. the REST paper "
                   "book) is NOT_ESTABLISHED under this rule.")},
    {"id": "LB8_VERDICT_AGE_AT_SUBMIT",
     "statement": ("The actual lane submits only while now - "
                   "verdict.evaluated_at <= %.1f s; otherwise it refuses %s."
                   % (MAX_VERDICT_AGE_AT_SUBMIT_S, R_VERDICT_STALE))},
]

NOT_ESTABLISHED_RESIDUALS = [
    {"id": "NE1_UNDELIVERED_CHANGES",
     "statement": ("Book changes made after the message's server stamp and "
                   "not yet delivered are not bounded: no change-to-delivery "
                   "bound is documented, and the undocumented "
                   "slow_consumer_skip_to_head parameter implies server-side "
                   "queueing of unstated delay. LB3 + LB4 bound only how old "
                   "the LAST DELIVERED book is.")},
    {"id": "NE2_DEBOUNCING_AND_COALESCING",
     "statement": ("Whether every change is published is not established for "
                   "the gRPC stream: 'sent on every change to the order book' "
                   "appears only on trader-guide/market-data (which describes "
                   "an HTTP-streaming product); retail debouncing batches "
                   "'at regular intervals' with no interval stated.")},
    {"id": "NE3_QUIET_BOOK_STALENESS",
     "statement": ("No heartbeat interval is documented and heartbeats carry "
                   "no timestamp and are per connection. A quiet book is "
                   "therefore bounded only by requiring a recent message for "
                   "THIS symbol (LB3); quiet markets will read STALE.")},
    {"id": "NE4_TRANSACT_TIME_EVENT",
     "statement": ("Whether transact_time is the matching-engine instant of "
                   "the last book change, the publisher's generation instant "
                   "or the gateway's send instant is not stated.")},
    {"id": "NE5_HOST_CLOCK_OFFSET",
     "statement": ("The deciding host's offset from venue time is not "
                   "measured by this rule; LB4 bounds offset plus delay "
                   "jointly.")},
    {"id": "NE6_INTRA_EPOCH_LOSS",
     "statement": ("Without a sequence number, a dropped or coalesced "
                   "message inside one epoch is undetectable; only epoch "
                   "breaks and timestamp regressions are. Full replacement "
                   "means the latest complete book supersedes any lost one.")},
    {"id": "NE7_RETAIL_ORDER_ROUTE_SAME_BOOK",
     "statement": ("That an order entered through the retail route executes "
                   "against the same book the institutional stream publishes "
                   "is the identity mapping's claim (LB6), not this rule's.")},
]

DOCUMENT: dict[str, Any] = {
    "rule_id": RULE_ID,
    "version": VERSION,
    "title": "Live book currentness from the resident institutional stream",
    "kind": "LIVE_BOOK_CURRENCY_RULE",
    "applies_to": (
        "a book read from the resident institutional gRPC market-data stream "
        "(institutional_stream.current(symbol)) for the institutional symbol "
        "exactly mapped to the retail contract being decided, evaluated in "
        "the deciding process at the decision instant; never a REST book "
        "read"),
    "bounds": {
        "max_receipt_age_s": MAX_RECEIPT_AGE_S,
        "max_venue_receipt_skew_s": MAX_VENUE_RECEIPT_SKEW_S,
        "max_verdict_age_at_actual_submit_s": MAX_VERDICT_AGE_AT_SUBMIT_S,
        "receipt_future_tolerance_s": RECEIPT_FUTURE_TOLERANCE_S,
        "tradable_instrument_states": list(TRADABLE_INSTRUMENT_STATES),
    },
    "bounds_rationale": (
        "2 s receipt age: five times tighter than the 10 s receipt bound the "
        "paper and actual lanes apply to the REST book, and well inside the "
        "stream's own 15 s liveness and 30 s snapshot bounds; with no "
        "documented heartbeat or re-snapshot interval, only a recent message "
        "for the symbol bounds a quiet book, so quiet books fail closed. "
        "2 s venue/receipt skew: an NTP-disciplined host and a healthy "
        "stream are milliseconds apart; anything beyond 2 s means a queued "
        "(slow-consumer) delivery or a clock fault, and both refuse. 2 s "
        "verdict age at submit: without it the 10 s decision-age allowance "
        "would let a 2 s-current book reach the venue 12 s old."),
    "requirements": [
        {"id": "C1_IDENTITY_EXACT", "fails_as": [NOT_ESTABLISHED, REFUSED],
         "statement": "LB6. Unmapped -> NOT_ESTABLISHED (%s); mapped to "
                      "another symbol or not EXACT -> REFUSED."
                      % R_IDENTITY_UNMAPPED},
        {"id": "C2_STREAM_RUNNING", "fails_as": [NOT_ESTABLISHED, REFUSED],
         "statement": "The stream runs in the deciding process; a venue "
                      "refusal of the credential is REFUSED."},
        {"id": "C3_CONNECTION_EPOCH_ALIVE", "fails_as": [NOT_ESTABLISHED, GAP],
         "statement": "LB1. No epoch -> NOT_ESTABLISHED; book held when the "
                      "epoch ended -> GAP."},
        {"id": "C4_COMPLETE_BOOK_ON_THIS_EPOCH",
         "fails_as": [NOT_ESTABLISHED, GAP],
         "statement": "LB2. A gap since the last complete book -> GAP; none "
                      "received yet on this epoch -> NOT_ESTABLISHED."},
        {"id": "C5_SEQUENCE_INTEGRITY", "fails_as": [],
         "statement": "VG6: " + SEQUENCE_NOT_PROVIDED + "; integrity rests on "
                      "C3, C4 and C7 (epoch + monotonic venue timestamp)."},
        {"id": "C6_VENUE_TS_PRESENT", "fails_as": [NOT_ESTABLISHED],
         "statement": "transact_time present on the book."},
        {"id": "C7_VENUE_TS_MONOTONIC_IN_EPOCH", "fails_as": [GAP],
         "statement": "LB4 monotonicity."},
        {"id": "C8_RECEIPT_AGE", "fails_as": [NOT_ESTABLISHED, STALE],
         "statement": "LB3."},
        {"id": "C9_VENUE_RECEIPT_SKEW", "fails_as": [NOT_ESTABLISHED, STALE],
         "statement": "LB4 skew: venue ts older than receipt by more than the "
                      "bound -> STALE; ahead of it by more -> NOT_ESTABLISHED."},
        {"id": "C10_MARKET_OPEN", "fails_as": [NOT_ESTABLISHED, REFUSED],
         "statement": "LB5 state. Unknown -> NOT_ESTABLISHED; any other "
                      "state -> REFUSED."},
        {"id": "C11_STREAM_BOOK_CURRENT",
         "fails_as": [NOT_ESTABLISHED, GAP, STALE, REFUSED],
         "statement": "institutional_stream.current() answered ok; its "
                      "refusal code is classified, never ignored."},
        {"id": "C12_PRICED_FROM_THIS_BOOK", "fails_as": [NOT_ESTABLISHED],
         "statement": "LB7."},
        {"id": "C13_VERDICT_AGE_AT_SUBMIT", "fails_as": ["ACTUAL_REFUSAL"],
         "statement": "LB8, enforced by execution_intent.ActualLane."},
    ],
    "verdict_rule": ("ESTABLISHED only when C1-C12 all pass; otherwise the "
                     "most severe failing class in the order REFUSED > GAP > "
                     "NOT_ESTABLISHED > STALE."),
    "sequence": SEQUENCE_NOT_PROVIDED,
    "evidence_split": {"venue_guaranteed": VENUE_GUARANTEED,
                       "locally_bounded": LOCALLY_BOUNDED,
                       "not_established": NOT_ESTABLISHED_RESIDUALS},
    "relation_to_review": (
        "This rule does NOT meet the activating documentation D1-D4 the "
        "2026-10-03 review reserved for a documented-timing rule (" + _REVIEW
        + " section 4); verdict B on documented timing stands. It is a "
        "locally bounded rule whose residual risks are the not_established "
        "items above; approving it is the owner accepting those residuals "
        "for the bounded actual lane, not a claim that the venue guarantees "
        "currency."),
    "authority_granted": "NONE",
    "authority_statement": (
        "This document grants no risk limit, capital authority, credential, "
        "account authority or submission switch. An owner approval makes the "
        "rule id admissible as a book-currency rule in actual_admission; every "
        "other admission requirement and every runtime gate (switch, account, "
        "cap, buying power, decision age, idempotency) is unchanged."),
    "approval_requirement": (
        "Status becomes APPROVED only on live_rule_artifacts with an owner "
        "approval record (owner_approval_actor, owner_approved_at, "
        "owner_approval_statement); the actor is never an agent or the "
        "creator; the canonical text and its sha256 are immutable. The rule "
        "is admissible only while the stored sha256 equals the deployed "
        "code's SHA256."),
}


def canonical_json(doc: dict) -> str:
    """The exact text that is hashed and stored. Pure."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def sha256_of(doc: dict) -> str:
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()


SHA256 = sha256_of(DOCUMENT)

#: THE LIVE BOOK RULES THIS CODE IMPLEMENTS: {rule_id: {version: sha256}}.
#: A stored approval admits a rule only when its (version, sha256) is here.
CODE_RULES: dict = {RULE_ID: {VERSION: SHA256}}


def document() -> dict:
    return copy.deepcopy(DOCUMENT)


def artifact() -> dict:
    return {"rule_id": RULE_ID, "version": VERSION, "sha256": SHA256,
            "status": "READY_FOR_OWNER_APPROVAL",
            "canonical_json": canonical_json(DOCUMENT),
            "document": document()}


# ═════════════════════════════════════════════════════════════════════
# THE EVALUATION (pure)
# ═════════════════════════════════════════════════════════════════════

def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _epoch_s(v):
    """A venue timestamp (aware datetime, ISO-8601 string or epoch seconds)
    -> epoch seconds, or None. A naive datetime is never guessed into UTC."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, datetime):
        return v.timestamp() if v.tzinfo else None
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.timestamp() if dt.tzinfo else None
    return None


def _r(v, n=3):
    return None if v is None else round(v, n)


def evaluate(*, stream_read: dict | None, identity: dict | None,
             now: float, us_market_slug: str | None = None,
             priced_from: dict | None = None) -> dict:
    """THE RULE. Pure; never raises on malformed input (it fails closed).

    stream_read  -- what `institutional_stream.current(symbol, now=now)`
                    returned (or None when no read was made);
    identity     -- the identity mapping's answer for the retail slug:
                    {"status": "EXACT", "symbol": <institutional symbol>, ...}
                    (anything else never passes);
    now          -- the evaluation instant (epoch seconds, our clock);
    priced_from  -- where the decision's executable price/depth came from:
                    {"source": "STREAM", "connection_epoch": n,
                     "received_at": t} for this stream observation, or e.g.
                    {"source": "REST_PAPER_BOOK", ...}.
    """
    comps: list = []

    def put(cid, ok, fail_class=None, reason=None, **ev):
        comps.append(dict({"component": cid, "passed": bool(ok),
                           "fails_as": None if ok else fail_class,
                           "reason": None if ok else reason}, **ev))

    at = _num(now)
    idn = identity if isinstance(identity, dict) else {}
    rd = stream_read if isinstance(stream_read, dict) else None
    ev = (rd or {}).get("evidence")
    ev = ev if isinstance(ev, dict) else {}
    conn = ev.get("connection") if isinstance(ev.get("connection"), dict) else {}
    mkt = ev.get("market") if isinstance(ev.get("market"), dict) else {}
    snap = ev.get("snapshot") if isinstance(ev.get("snapshot"), dict) else {}
    gap = ev.get("gap") if isinstance(ev.get("gap"), dict) else None
    sym = (rd or {}).get("symbol") or mkt.get("symbol")
    stream_state = ev.get("stream_state")
    seq = conn.get("seq")
    connected = conn.get("connected") is True
    book_seq = snap.get("book_seq")
    recv = _num(snap.get("received_at"))
    vts = _epoch_s(snap.get("venue_ts"))

    # C1 · exact contract identity
    i_status = str(idn.get("status") or "").upper()
    i_sym = idn.get("symbol")
    if not idn or i_status in ("", "NOT_ESTABLISHED", "UNMAPPED", "NONE") \
            or not i_sym:
        put("C1_IDENTITY_EXACT", False, NOT_ESTABLISHED, R_IDENTITY_UNMAPPED,
            identity_status=idn.get("status"), symbol=i_sym)
    elif i_status != "EXACT" or (rd is not None and sym != i_sym):
        put("C1_IDENTITY_EXACT", False, REFUSED, R_IDENTITY_MISMATCH,
            identity_status=idn.get("status"), symbol=i_sym, book_symbol=sym)
    else:
        put("C1_IDENTITY_EXACT", True, identity_status="EXACT", symbol=i_sym,
            binding=idn.get("binding_sha") or idn.get("evidence"))

    if rd is None or not ev:
        for cid in ("C2_STREAM_RUNNING", "C3_CONNECTION_EPOCH_ALIVE",
                    "C4_COMPLETE_BOOK_ON_THIS_EPOCH", "C6_VENUE_TS_PRESENT",
                    "C7_VENUE_TS_MONOTONIC_IN_EPOCH", "C8_RECEIPT_AGE",
                    "C9_VENUE_RECEIPT_SKEW", "C10_MARKET_OPEN",
                    "C11_STREAM_BOOK_CURRENT"):
            put(cid, False, NOT_ESTABLISHED, R_NO_STREAM_READ)
    else:
        # C2 · the stream runs in this process
        if stream_state in _STREAM_REFUSED:
            put("C2_STREAM_RUNNING", False, REFUSED, R_STREAM_REFUSED,
                stream_state=stream_state)
        elif stream_state not in _STREAM_RUNNING:
            put("C2_STREAM_RUNNING", False, NOT_ESTABLISHED,
                R_STREAM_NOT_RUNNING, stream_state=stream_state)
        else:
            put("C2_STREAM_RUNNING", True, stream_state=stream_state)
        # C3 · connection epoch alive
        seq_ok = isinstance(seq, int) and not isinstance(seq, bool) and seq > 0
        if connected and seq_ok:
            put("C3_CONNECTION_EPOCH_ALIVE", True, connection_epoch=seq,
                connection_id=conn.get("id"))
        elif book_seq is not None or (gap and gap.get("reason")
                                      == _GAP_CONNECTION):
            put("C3_CONNECTION_EPOCH_ALIVE", False, GAP, R_EPOCH_LOST,
                connection_epoch=seq, connected=connected)
        else:
            put("C3_CONNECTION_EPOCH_ALIVE", False, NOT_ESTABLISHED,
                R_NO_EPOCH, connection_epoch=seq, connected=connected)
        # C4 · a complete book on THIS epoch after connect/reconnect/gap
        on_epoch = (seq_ok and book_seq == seq
                    and snap.get("on_current_connection") is True)
        if on_epoch and gap is None:
            put("C4_COMPLETE_BOOK_ON_THIS_EPOCH", True, book_epoch=book_seq)
        elif gap is not None or (book_seq is not None and book_seq != seq):
            put("C4_COMPLETE_BOOK_ON_THIS_EPOCH", False, GAP, R_GAP_RECONNECT,
                book_epoch=book_seq, connection_epoch=seq,
                gap_reason=(gap or {}).get("reason"))
        else:
            put("C4_COMPLETE_BOOK_ON_THIS_EPOCH", False, NOT_ESTABLISHED,
                R_SNAPSHOT_PENDING, book_epoch=book_seq, connection_epoch=seq)
        # C6 · venue timestamp present
        if vts is None:
            put("C6_VENUE_TS_PRESENT", False, NOT_ESTABLISHED, R_NO_VENUE_TS)
        else:
            put("C6_VENUE_TS_PRESENT", True, venue_ts=snap.get("venue_ts"))
        # C7 · monotonic within the epoch
        if gap is not None and gap.get("reason") == _GAP_CLOCK:
            put("C7_VENUE_TS_MONOTONIC_IN_EPOCH", False, GAP, R_TS_BACKWARDS,
                high_water=gap.get("high_water"),
                received_venue_ts=gap.get("received_venue_ts"))
        else:
            put("C7_VENUE_TS_MONOTONIC_IN_EPOCH", True,
                regressions_total=ev.get("regressions"))
        # C8 · local receipt age
        age = None if (recv is None or at is None) else at - recv
        if age is None:
            put("C8_RECEIPT_AGE", False, NOT_ESTABLISHED, R_NO_RECEIPT,
                bound_s=MAX_RECEIPT_AGE_S)
        elif age < -RECEIPT_FUTURE_TOLERANCE_S:
            put("C8_RECEIPT_AGE", False, NOT_ESTABLISHED, R_RECEIPT_FUTURE,
                receipt_age_s=_r(age), bound_s=MAX_RECEIPT_AGE_S)
        elif age > MAX_RECEIPT_AGE_S:
            put("C8_RECEIPT_AGE", False, STALE, R_RECEIPT_OLD,
                receipt_age_s=_r(age), bound_s=MAX_RECEIPT_AGE_S)
        else:
            put("C8_RECEIPT_AGE", True, receipt_age_s=_r(age),
                bound_s=MAX_RECEIPT_AGE_S)
        # C9 · venue timestamp within a bound of local receipt
        skew = None if (recv is None or vts is None) else recv - vts
        if skew is None:
            put("C9_VENUE_RECEIPT_SKEW", False, NOT_ESTABLISHED,
                R_NO_VENUE_TS if vts is None else R_NO_RECEIPT,
                bound_s=MAX_VENUE_RECEIPT_SKEW_S)
        elif skew > MAX_VENUE_RECEIPT_SKEW_S:
            put("C9_VENUE_RECEIPT_SKEW", False, STALE, R_SKEW_LATE,
                venue_receipt_skew_s=_r(skew),
                bound_s=MAX_VENUE_RECEIPT_SKEW_S)
        elif skew < -MAX_VENUE_RECEIPT_SKEW_S:
            put("C9_VENUE_RECEIPT_SKEW", False, NOT_ESTABLISHED, R_SKEW_AHEAD,
                venue_receipt_skew_s=_r(skew),
                bound_s=MAX_VENUE_RECEIPT_SKEW_S)
        else:
            put("C9_VENUE_RECEIPT_SKEW", True, venue_receipt_skew_s=_r(skew),
                bound_s=MAX_VENUE_RECEIPT_SKEW_S)
        # C10 · market open / tradable
        mstate = mkt.get("state")
        if not mstate:
            put("C10_MARKET_OPEN", False, NOT_ESTABLISHED, R_STATE_UNKNOWN)
        elif str(mstate) not in TRADABLE_INSTRUMENT_STATES:
            put("C10_MARKET_OPEN", False, REFUSED, R_NOT_OPEN,
                market_state=mstate, state_source=mkt.get("state_source"))
        else:
            put("C10_MARKET_OPEN", True, market_state=mstate,
                state_source=mkt.get("state_source"))
        # C11 · the stream's own currency answer
        if rd.get("ok") is True and rd.get("book") is not None:
            put("C11_STREAM_BOOK_CURRENT", True)
        else:
            code = rd.get("refusal")
            put("C11_STREAM_BOOK_CURRENT", False,
                STREAM_REFUSAL_CLASS.get(code, NOT_ESTABLISHED),
                R_STREAM_NOT_CURRENT, stream_refusal=code,
                stream_why=rd.get("why"))
    # C5 · sequence integrity: not provided; rests on C3/C4/C7
    vseq = ev.get("venue_sequence") or "NOT_PROVIDED_BY_VENUE"
    put("C5_SEQUENCE_INTEGRITY", True, status=SEQUENCE_NOT_PROVIDED,
        venue_sequence=vseq,
        relies_on=["C3_CONNECTION_EPOCH_ALIVE",
                   "C4_COMPLETE_BOOK_ON_THIS_EPOCH",
                   "C7_VENUE_TS_MONOTONIC_IN_EPOCH"])
    # C12 · the decision priced from THIS book
    pf = priced_from if isinstance(priced_from, dict) else None
    if pf is None:
        put("C12_PRICED_FROM_THIS_BOOK", False, NOT_ESTABLISHED,
            R_PRICE_NOT_STATED)
    elif (str(pf.get("source")) == "STREAM" and rd is not None
          and pf.get("connection_epoch") == seq and recv is not None
          and _num(pf.get("received_at")) == recv):
        put("C12_PRICED_FROM_THIS_BOOK", True, priced_from="STREAM")
    else:
        put("C12_PRICED_FROM_THIS_BOOK", False, NOT_ESTABLISHED,
            R_PRICE_OTHER_BOOK, priced_from=pf.get("source"))

    comps.sort(key=lambda c: int(c["component"].split("_")[0][1:]))
    failed = [c for c in comps if not c["passed"]]
    verdict, reason = ESTABLISHED, None
    for cls in PRECEDENCE:
        hit = [c for c in failed if c["fails_as"] == cls]
        if hit:
            verdict, reason = cls, hit[0]["reason"]
            break
    # the stream book's own verdict, before C12 binds it to the priced book
    stream_verdict = ESTABLISHED
    for cls in PRECEDENCE:
        if any(c["fails_as"] == cls for c in failed
               if c["component"] != "C12_PRICED_FROM_THIS_BOOK"):
            stream_verdict = cls
            break
    receipt_age = None if (recv is None or at is None) else at - recv
    venue_age = None if (vts is None or at is None) else at - vts
    subscription = (SUBSCRIPTION_RUNNING
                    if (rd is not None and stream_state in _STREAM_RUNNING
                        and connected) else (stream_state or "NOT_SUBSCRIBED"))
    return {
        "rule": RULE_ID, "rule_version": VERSION, "rule_sha256": SHA256,
        "verdict": verdict, "reason": reason,
        "stream_book_verdict": stream_verdict,
        "evaluated_at": at,
        "us_market_slug": us_market_slug, "symbol": i_sym or sym,
        "subscription_state": subscription,
        "connection_epoch": seq if rd is not None else None,
        "connection_id": conn.get("id"),
        "book_epoch": book_seq,
        "receipt_age_s": _r(receipt_age),
        "venue_ts_age_s": _r(venue_age),
        "venue_receipt_skew_s": _r(None if (recv is None or vts is None)
                                   else recv - vts),
        "gap_since_snapshot": bool(gap) or (
            rd is not None and book_seq is not None and book_seq != seq),
        "venue_sequence": SEQUENCE_NOT_PROVIDED,
        "market_state": mkt.get("state"),
        "components": comps,
        "failed_components": [c["component"] for c in failed],
        "bounds": dict(DOCUMENT["bounds"]),
    }


def admission_record(v: dict) -> dict:
    """The compact record `admission_facts.book.book_currency` carries for
    actual_admission (verdict, rule, subscription_state, ...), with the
    evidence split summarised by id. Pure."""
    v = dict(v or {})
    return {
        "verdict": v.get("verdict", NOT_ESTABLISHED),
        "rule": v.get("rule", RULE_ID),
        "rule_version": v.get("rule_version", VERSION),
        "rule_sha256": v.get("rule_sha256", SHA256),
        "reason": v.get("reason"),
        "stream_book_verdict": v.get("stream_book_verdict"),
        "evaluated_at": v.get("evaluated_at"),
        "us_market_slug": v.get("us_market_slug"),
        "symbol": v.get("symbol"),
        "subscription_state": v.get("subscription_state"),
        "connection_epoch": v.get("connection_epoch"),
        "receipt_age_s": v.get("receipt_age_s"),
        "venue_ts_age_s": v.get("venue_ts_age_s"),
        "venue_receipt_skew_s": v.get("venue_receipt_skew_s"),
        "gap_since_snapshot": bool(v.get("gap_since_snapshot")),
        "venue_sequence": SEQUENCE_NOT_PROVIDED,
        "market_state": v.get("market_state"),
        "failed_components": list(v.get("failed_components") or []),
        "bounds": dict(DOCUMENT["bounds"]),
        "venue_guaranteed": [x["id"] for x in VENUE_GUARANTEED],
        "locally_bounded": [x["id"] for x in LOCALLY_BOUNDED],
        "not_established": [x["id"] for x in NOT_ESTABLISHED_RESIDUALS],
        "evidence_basis": (
            "venue_guaranteed = only what the cited venue pages state; "
            "locally_bounded = our receipt clock and connection-epoch logic; "
            "not_established = undelivered changes, debouncing, quiet-book "
            "staleness beyond a recent message, transact_time's event, host "
            "clock offset, intra-epoch loss, same-book retail route"),
    }


def not_evaluated(*, reason: str, us_market_slug: str | None = None,
                  now: float | None = None, identity: dict | None = None
                  ) -> dict:
    """A NOT_ESTABLISHED verdict when the rule could not be run at all
    (identity unmapped, reader failed). Same shape as `evaluate`."""
    v = evaluate(stream_read=None, identity=identity, now=now or 0.0,
                 us_market_slug=us_market_slug, priced_from=None)
    v.update(verdict=NOT_ESTABLISHED, reason=reason,
             stream_book_verdict=NOT_ESTABLISHED, evaluated_at=_num(now))
    return v


def verdict_age_refusal(bc: dict | None, *, now: float) -> dict | None:
    """C13 / LB8 for the actual lane: None when the recorded live verdict was
    evaluated within MAX_VERDICT_AGE_AT_SUBMIT_S of `now`; otherwise the
    refusal with its evidence. Only applies to this rule's records."""
    bc = dict(bc or {})
    if bc.get("rule") != RULE_ID:
        return None
    ev_at = _num(bc.get("evaluated_at"))
    if ev_at is None:
        return {"refusal": R_VERDICT_STALE, "verdict_age_s": None,
                "limit_s": MAX_VERDICT_AGE_AT_SUBMIT_S,
                "why": "the live verdict carries no evaluation instant"}
    age = float(now) - ev_at
    if age > MAX_VERDICT_AGE_AT_SUBMIT_S or age < -RECEIPT_FUTURE_TOLERANCE_S:
        return {"refusal": R_VERDICT_STALE, "verdict_age_s": round(age, 3),
                "limit_s": MAX_VERDICT_AGE_AT_SUBMIT_S}
    return None


#: The identity mapping contract the decision path accepts (pluggable; the
#: integrator installs the institutional slug<->symbol mapper here or in
#: ctx["live_book_identity"]). Signature: mapper(slug, order_intent) -> dict
#: with status "EXACT" and the institutional "symbol" for an exact binding.
IdentityMapper = Callable[[str, "str | None"], dict]
