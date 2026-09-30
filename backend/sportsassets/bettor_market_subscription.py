"""THE DECISION PROCESS'S OWN MARKET-DATA SUBSCRIPTION, WITH READINESS.

WHAT WAS THERE, AND WHAT WAS MISSING
------------------------------------

`bettor_market_stream.MarketStream` speaks the venue's websocket protocol:
connect, subscribe in documented batches, full-replacement books with both
clocks, connection epochs, a reconnect loop. `bettor_stream_currency` holds the
M1 predicate (P1-P6) and `ext_pinnacle_loop.book_currency_evidence` is the one
seam through which a mechanism reaches `venue_quote`.

Four things were missing, and together they are why the seam could never
supply M1 even in principle:

  1. NOBODY SUBSCRIBED IN THE PROCESS THAT DECIDES. The entry lane runs in the
     API process; every `MarketStream` runs in the workers process. P2-P4 are
     facts about a socket in THE DECIDING PROCESS, so they were unmet there by
     construction, whatever the venue documents (see
     research/evidence/VENUE_TIMING_SUPPORT_REQUEST_2026-09-29.md §(d)3).
  2. NO PER-MARKET READINESS. "Subscribed" was a set membership. Whether a
     given market's book was on the current connection, awaiting its first
     snapshot, cut off by a drop, silent, or refused by the venue was nowhere
     queryable -- so a refusal could only ever say NOT_ESTABLISHED.
  3. THE RECONNECT LOOP WAS UNBOUNDED, and treated a venue refusal of the key
     (HTTP 401/403 at the handshake) like a network blip: one attempt every
     30 s, forever, with the refusal itself visible only in a log.
  4. NOTHING REPORTED IT. The heartbeat carried no subscription state at all.

WHAT THIS MODULE IS
-------------------

A supervisor around ONE `MarketStream` (with a bounded reconnect policy and no
trade subscription), fed by the markets the decision process actually asks
about, holding an explicit readiness state per market:

    NOT_SUBSCRIBED     the decision process has not asked for this market, or
                       the subscription is not running in this process
    SUBSCRIBING        asked for; the subscribe message has not yet been sent
                       on the current connection (queued, or no connection)
    SNAPSHOT_PENDING   the subscribe was sent on THIS connection and no full
                       book has arrived on it yet -- including after a gap
    CURRENT            a full book for this market arrived on the current
                       connection, after any gap, within the snapshot bound,
                       on a connection that proved itself alive within the
                       silence bound
    STALE              a book is held on this connection, but the connection
                       has gone silent past its bound, or the book is older
                       than the snapshot bound
    GAP                our observation of this market has a hole in it: the
                       connection dropped, the reconnect bound was exhausted,
                       or the venue's clock for this market went BACKWARDS
    REFUSED_BY_VENUE   the venue refused the handshake (401/403) or answered
                       this market's subscribe request with an error

Every state carries a NAMED reason (the R_* constants below) and a sentence.

WHAT "CURRENT" IS, AND -- THE PART THAT MATTERS -- WHAT IT IS NOT
----------------------------------------------------------------

CURRENT is P2 (liveness), P3 (connection continuity) and P4 (identity) made
per-market and queryable. It is NOT a timing certificate. `market_data_design`
lists "a clean reconnect and resubscribe" as the fifth rejected freshness
predicate, and this module does not revive it: a market can be CURRENT here and
its book still refused, because P5 -- the venue documenting how current a
message is -- is a property of the published protocol that no subscription
supplies. `bettor_stream_currency.evidence_for` remains the one gate and
requires BOTH its own P1-P6 and, when this subscription is installed, CURRENT.

WHY GAP DETECTION IS EPOCH- AND CLOCK-BASED, NOT SEQUENCE-BASED. The payload
carries no sequence number (pinned in test_m1_is_verified_not_asserted §1). So
a gap is detected the ways that exist: (a) a connection drop, which starts a
new epoch and returns every market to SNAPSHOT_PENDING until a full book
arrives on the new connection; (b) the venue's own `transactTime` for a market
moving BACKWARDS on one connection, which is out-of-order evidence whatever the
field denotes -- that market is GAP until a later full book arrives whose clock
is at or past the high-water mark. No resubscribe is sent for (b): the
protocol documents no per-market resnapshot request, and a duplicate subscribe
whose venue behaviour is unverified could come back as an error that would be
misread as a refusal. The next authoritative full replacement IS the resnapshot
on a full-replacement feed.

FAIL CLOSED. Not installed, disabled, no credentials, not started, stopped,
gave up, refused: every one of them answers "not CURRENT" with its reason, and
the decision path then refuses exactly as it did before this module existed.

NO ORDER PATH. This module imports no order function and reads no position.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from . import bettor_market_stream as ms
from . import bettor_stream_currency as sc

log = logging.getLogger(__name__)

SUBSCRIPTION_VERSION = "BETTOR_MARKET_SUBSCRIPTION_V1"

#: ARMED BY AN ENVIRONMENT FLAG, AND OFF UNLESS IT SAYS ON.
#:
#: Why off by default. This opens a NEW outbound websocket to the venue from
#: the API process, signed with the same key the workers process uses. Whether
#: the venue limits concurrent market-data connections per key is not
#: documented, and the workers process runs a protected stream on that key. A
#: connection that could displace a protected one is armed by a person, the way
#: `EXT_PINNACLE_SHADOW` arms the loop that consults it -- not by a deploy.
ENV_FLAG = "BETTOR_MARKET_SUBSCRIPTION"
ENV_ON = ("on", "1", "true", "yes")

# ── readiness states ────────────────────────────────────────────────
NOT_SUBSCRIBED = "NOT_SUBSCRIBED"
SUBSCRIBING = "SUBSCRIBING"
SNAPSHOT_PENDING = "SNAPSHOT_PENDING"
CURRENT = "CURRENT"
STALE = "STALE"
GAP = "GAP"
REFUSED_BY_VENUE = "REFUSED_BY_VENUE"
READINESS_STATES = (NOT_SUBSCRIBED, SUBSCRIBING, SNAPSHOT_PENDING, CURRENT,
                    STALE, GAP, REFUSED_BY_VENUE)

# ── named reasons ───────────────────────────────────────────────────
R_NOT_RUNNING = "SUBSCRIPTION_NOT_RUNNING_IN_THIS_PROCESS"
R_NOT_REQUESTED = "MARKET_NOT_REQUESTED_BY_THE_DECISION_PROCESS"
R_AWAITING_CONNECTION = "AWAITING_CONNECTION"
R_AWAITING_SEND = "SUBSCRIBE_NOT_YET_SENT_ON_THIS_CONNECTION"
R_AWAITING_FIRST_BOOK = "AWAITING_FIRST_FULL_BOOK_ON_THIS_CONNECTION"
R_AWAITING_RESNAPSHOT = "AWAITING_RESNAPSHOT_AFTER_A_GAP"
R_CURRENT = "FULL_BOOK_ON_A_LIVE_CONNECTION_WITHIN_BOUND"
R_SILENT = "CONNECTION_SILENT_PAST_THE_LIVENESS_BOUND"
R_BOOK_OLD = "NO_FULL_BOOK_WITHIN_THE_SNAPSHOT_BOUND"
R_CONNECTION_LOST = "CONNECTION_LOST_BOOK_NOT_CARRIED_ACROSS"
R_SOURCE_CLOCK_REGRESSED = "VENUE_CLOCK_WENT_BACKWARDS_FOR_THIS_MARKET"
R_GAVE_UP = "RECONNECT_BOUND_EXHAUSTED"
R_VENUE_REFUSED_KEY = "VENUE_REFUSED_THE_HANDSHAKE"
R_VENUE_ENTITLEMENT = "VENUE_DENIED_ENTITLEMENT_FOR_THIS_MARKET"
R_VENUE_MARKET_UNKNOWN = "VENUE_DOES_NOT_RECOGNISE_THIS_MARKET"
R_VENUE_ERROR = "VENUE_ANSWERED_THE_SUBSCRIBE_WITH_AN_ERROR"

# ── the subscription's own state ────────────────────────────────────
S_DISABLED = "DISABLED_BY_CONFIGURATION"
S_NO_CREDENTIALS = "NO_VENUE_CREDENTIALS_CONFIGURED"
S_NOT_STARTED = "NOT_STARTED"
S_CONNECTING = "CONNECTING"
S_CONNECTED = "CONNECTED"
S_RECONNECTING = "RECONNECTING"
S_GAVE_UP = "GAVE_UP"
S_REFUSED_BY_VENUE = "REFUSED_BY_VENUE"
S_STOPPED = "STOPPED"
S_WAITING_FOR_DEDICATED_KEY = "WAITING_FOR_DEDICATED_MARKET_DATA_KEY"

#: WHICH CREDENTIAL THE SUBSCRIPTION MAY USE. "dedicated" (the default) uses
#: only PMUS_MD_KEY_ID/PMUS_MD_SECRET_KEY -- a SEPARATE ORDINARY Polymarket US
#: API key used for market data -- and waits, by name, when they are absent.
#: "shared" uses the venue key the protected worker also streams with and must
#: be chosen explicitly. WHAT THE SEPARATION DOES AND DOES NOT DO: it gives
#: separate revocation and MAY isolate limits the venue applies per key; it
#: does NOT establish protection from limits applied per account, participant,
#: endpoint or IP, none of which is established. This application path only
#: reads, but that does not make the credential itself read-only: the key's
#: permissions are whatever the venue grants and enforces. Its authentication
#: is verified only by the connection itself (a 401/403 reads REFUSED_BY_VENUE),
#: never by the length of the configured value.
ENV_KEY_SOURCE = "BETTOR_MARKET_SUBSCRIPTION_KEY"
KEY_DEDICATED = "dedicated"
KEY_SHARED = "shared"


def credential_source() -> str:
    v = str(os.environ.get(ENV_KEY_SOURCE, "")).strip().lower()
    return KEY_SHARED if v == KEY_SHARED else KEY_DEDICATED
RUNNING_STATES = (S_CONNECTING, S_CONNECTED, S_RECONNECTING, S_GAVE_UP,
                  S_REFUSED_BY_VENUE)

# ── what the decision path names when M1 is not supplied ────────────
#: The refusal the decision still reaches is VENUE_BOOK_CURRENCY_NOT_ESTABLISHED
#: -- unchanged, so the calibration-only path and every tally keyed on it are
#: untouched. These say WHICH part of M1 was missing, beside it.
M1_SUBSCRIPTION_NOT_RUNNING = "M1_SUBSCRIPTION_NOT_RUNNING"
M1_MARKET_NOT_SUBSCRIBED = "M1_MARKET_NOT_SUBSCRIBED"
M1_SUBSCRIPTION_PENDING = "M1_SUBSCRIPTION_PENDING"
M1_SNAPSHOT_PENDING = "M1_SNAPSHOT_PENDING"
M1_SUBSCRIPTION_STALE = "M1_SUBSCRIPTION_STALE"
M1_SUBSCRIPTION_GAP = "M1_SUBSCRIPTION_GAP"
M1_SUBSCRIPTION_REFUSED_BY_VENUE = "M1_SUBSCRIPTION_REFUSED_BY_VENUE"
#: The subscription is CURRENT and the feed-level precondition is not: the
#: venue has not documented its timing (P5). This is where the P5 answer lands.
M1_FEED_TIMING_NOT_DOCUMENTED = "M1_FEED_TIMING_NOT_DOCUMENTED_P5"
#: CURRENT here, P1-P6 met as a feed, and still not established when the
#: verdict was taken (per-market P2-P4 in the currency module, or the
#: evaluator's bounds re-aged to the verdict instant).
M1_NOT_ESTABLISHED_AT_THE_VERDICT = "M1_NOT_ESTABLISHED_AT_THE_VERDICT_INSTANT"
M1_REFUSAL_BY_STATE = {
    SUBSCRIBING: M1_SUBSCRIPTION_PENDING,
    SNAPSHOT_PENDING: M1_SNAPSHOT_PENDING,
    STALE: M1_SUBSCRIPTION_STALE,
    GAP: M1_SUBSCRIPTION_GAP,
    REFUSED_BY_VENUE: M1_SUBSCRIPTION_REFUSED_BY_VENUE,
}

# ── bounds: NONE NEW ────────────────────────────────────────────────
#: The liveness and snapshot bounds are the currency module's, which are the
#: lane's. This module introduces no third number for either.
MAX_SILENCE_S = sc.MAX_SILENCE_S
MAX_SNAPSHOT_AGE_S = sc.MAX_SNAPSHOT_AGE_S

#: The reconnect bound this subscription runs under (see
#: `bettor_market_stream.RECONNECT_POLICY_DEFAULTS` for what each field bounds).
RECONNECT_POLICY = dict(ms.RECONNECT_POLICY_DEFAULTS)

#: AFTER THE BOUND IS SPENT, HOW LONG BEFORE ONE FRESH TRY. A venue outage of
#: an hour must not disable the subscription until the next deploy, and a spent
#: bound must not become a retry loop by another name. So: one fresh stream --
#: itself bounded -- at most once per this interval, and only when the decision
#: process is actually asking for markets.
RESTART_AFTER_GAVE_UP_S = 900.0
#: AFTER THE VENUE REFUSED THE KEY, the same, but slower: one handshake per
#: half hour, so an entitlement the venue grants later is picked up without a
#: redeploy, and a refusal is never hammered.
RESTART_AFTER_VENUE_REFUSAL_S = 1800.0
#: A market the decision process has not asked about for this long is dropped
#: from the wanted set. Four scheduled cycles (`ext_pinnacle_loop.CYCLE_S`).
WANT_TTL_S = 3600.0
#: The stream's own cap on markets held at once.
MAX_MARKETS = ms.MAX_SUBSCRIPTIONS

#: Heartbeat sample sizes, so the digest stays a heartbeat.
DIGEST_SAMPLE = 10
ERROR_RING = 20

_ENTITLEMENT_WORDS = ("unauthori", "forbidden", "permission", "entitle",
                      "not allowed", "not permitted", "access denied",
                      "denied")
_UNKNOWN_MARKET_WORDS = ("not found", "unknown market", "invalid market",
                         "no such market", "does not exist")


def classify_venue_error(text) -> str:
    """A venue error string -> the named reason. Matched on lower-cased words;
    anything unrecognised is still a refusal, named as the generic one."""
    t = str(text or "").lower()
    if any(w in t for w in _ENTITLEMENT_WORDS):
        return R_VENUE_ENTITLEMENT
    if any(w in t for w in _UNKNOWN_MARKET_WORDS):
        return R_VENUE_MARKET_UNKNOWN
    return R_VENUE_ERROR


class MarketSubscription:
    """One supervised stream and the readiness of every market it holds.

    Thread-safe: the stream calls in from its socket thread; the decision
    process reads from the event loop. Every public read takes `_lock` and
    returns plain data. Nothing here raises into either caller.
    """

    def __init__(self, key_id: str, secret_key: str, *, stream_factory=None,
                 ws_factory=None, clock=None, policy=None) -> None:
        # Held only to hand to the stream's constructor -- the existing
        # credential path. Never logged, never placed in any report.
        self._key_id = key_id
        self._secret_key = secret_key
        self._stream_factory = stream_factory or ms.MarketStream
        self._ws_factory = ws_factory
        self._clock = clock or time.time
        self._policy = dict(policy or RECONNECT_POLICY)
        self._lock = threading.Lock()
        self.stream = None
        self.state = S_NOT_STARTED
        self.state_why = "constructed; start() has not been called"
        self.state_since = self._clock()
        # A CONNECTION SEQUENCE THAT SURVIVES A RESTART. The stream's `epoch`
        # restarts at 1 in a new stream object; books are tagged with this
        # instead, so a book from a previous stream can never match.
        self._conn_seq = 0
        self._connected = False
        self._connected_at = None
        self._last_life_at = None
        self._last_disconnect = None
        self._markets: dict = {}
        self._errors: list = []
        self._starts = 0
        self._restarts: list = []
        self._venue_refusal = None
        self._gave_up = None
        self._run_thread = True

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self, *, run_thread: bool = True) -> dict:
        """Build the stream, subscribe the wanted markets, start the socket.

        `run_thread=False` builds everything but leaves the socket loop for
        the caller to drive (`asyncio.run(sub.stream._main())`), which is how
        the deterministic tests run the REAL loop on a fake transport."""
        with self._lock:
            if self.state in (S_CONNECTING, S_CONNECTED, S_RECONNECTING):
                return {"started": False, "why": "already running",
                        "state": self.state}
            self._run_thread = bool(run_thread)
            wanted = list(self._markets)
            self._starts += 1
            self._venue_refusal = None
            self._gave_up = None
        try:
            stream = self._stream_factory(
                self._key_id, self._secret_key, on_book=self._on_book,
                ws_factory=self._ws_factory, reconnect_policy=self._policy,
                with_trades=False)
            stream.set_lifecycle_listener(self._on_lifecycle)
            stream.set_heartbeat_listener(self._on_heartbeat)
            if wanted:
                stream.subscribe(wanted)
        except Exception as exc:  # noqa: BLE001 -- named, not raised
            self._set_state(S_STOPPED, "the stream could not be built: %s"
                            % type(exc).__name__)
            return {"started": False, "why": type(exc).__name__}
        with self._lock:
            self.stream = stream
            for rec in self._markets.values():
                rec["sent_seq"] = None
                rec["refused"] = None
        self._set_state(S_CONNECTING, "stream built; socket starting")
        if run_thread:
            try:
                stream.start()
            except Exception as exc:  # noqa: BLE001
                self._set_state(S_STOPPED, "the socket thread did not start: "
                                "%s" % type(exc).__name__)
                return {"started": False, "why": type(exc).__name__}
        return {"started": True, "state": self.state, "markets": len(wanted)}

    def stop(self, *, wait_s: float = 5.0) -> dict:
        """Clean shutdown: the stream's own measured stop, then STOPPED."""
        with self._lock:
            stream = self.stream
        got = None
        if stream is not None:
            try:
                got = stream.stop(wait_s=wait_s)
            except Exception as exc:  # noqa: BLE001
                got = {"shutdown": "STOP_RAISED",
                       "error": type(exc).__name__}
        with self._lock:
            self._connected = False
        self._set_state(S_STOPPED, "stop() was called")
        return {"stopped": True, "stream": got}

    def _set_state(self, state: str, why: str) -> None:
        with self._lock:
            if state != self.state:
                self.state_since = self._clock()
            self.state = state
            self.state_why = why

    def _maybe_restart(self, now: float) -> None:
        """ONE fresh, bounded stream after a spent bound or a refusal, no
        more often than the interval for that case. Called from `want()`, so
        it happens only while the decision process is asking for markets."""
        with self._lock:
            state, since = self.state, self.state_since
        wait = (RESTART_AFTER_GAVE_UP_S if state == S_GAVE_UP else
                RESTART_AFTER_VENUE_REFUSAL_S if state == S_REFUSED_BY_VENUE
                else None)
        if wait is None or now - since < wait:
            return
        with self._lock:
            self._restarts.append({"at": now, "after": state})
            del self._restarts[:-20]
            old = self.stream
        if old is not None:
            try:
                old.stop(wait_s=0.0)
            except Exception:  # noqa: BLE001
                pass
        log.info("bettor market subscription: one fresh attempt after %s", state)
        self.start(run_thread=self._run_thread)

    # ── what the decision process asks for ───────────────────────────

    def want(self, slugs, *, now=None) -> dict:
        """The decision process needs these markets. Queue the new ones,
        refresh the rest, prune what nobody has asked about for WANT_TTL_S."""
        at = float(now if now is not None else self._clock())
        fresh, dropped = [], 0
        with self._lock:
            for s in slugs or ():
                s = str(s or "")
                if not s:
                    continue
                rec = self._markets.get(s)
                if rec is None:
                    if len(self._markets) >= MAX_MARKETS:
                        dropped += 1
                        continue
                    rec = self._markets[s] = _new_market(at)
                    fresh.append(s)
                rec["wanted_at"] = at
            gone = [s for s, r in self._markets.items()
                    if at - r["wanted_at"] > WANT_TTL_S]
            for s in gone:
                self._markets.pop(s, None)
            keep = list(self._markets)
            stream = self.stream
        if stream is not None:
            try:
                if gone:
                    stream.prune(keep)
                if fresh:
                    stream.subscribe(fresh)
            except Exception as exc:  # noqa: BLE001
                log.debug("subscription want() failed: %s", exc)
        self._maybe_restart(at)
        return {"queued": len(fresh), "pruned": len(gone),
                "dropped_over_cap": dropped}

    # ── the stream's events (socket thread) ──────────────────────────

    def _on_lifecycle(self, event: str, info: dict) -> None:
        now = self._clock()
        if event == "connected":
            with self._lock:
                self._conn_seq += 1
                self._connected = True
                self._connected_at = now
                self._last_life_at = now
                # EVERY MARKET MUST BE RE-ASKED ON THIS SOCKET. `sent_seq` is
                # the connection a subscribe went out on; a new connection
                # makes every old one stale, so nothing reads as sent here
                # until the stream says it was.
                for rec in self._markets.values():
                    rec["source_hw"] = None
            self._set_state(S_CONNECTED, "connected (attempt %s)"
                            % info.get("attempt"))
        elif event == "disconnected":
            with self._lock:
                self._connected = False
                self._last_disconnect = {"at": now, "why": info.get("why"),
                                         "delivered": info.get("delivered")}
                for rec in self._markets.values():
                    if rec.get("book_seq") is not None or rec.get("gap"):
                        # THE BOOK IS NOT CARRIED ACROSS. It stays GAP until a
                        # full book arrives on a LATER connection.
                        rec["gap"] = {"reason": R_CONNECTION_LOST, "at": now,
                                      "seq": self._conn_seq}
            self._set_state(S_RECONNECTING, "connection lost: %s"
                            % info.get("why"))
        elif event == "subscribe_sent":
            if info.get("kind") != "book":
                return
            with self._lock:
                for s in info.get("slugs") or ():
                    rec = self._markets.get(s)
                    if rec is not None:
                        rec["sent_seq"] = self._conn_seq
                        rec["sent_at"] = now
                        rec["request_id"] = info.get("request_id")
        elif event == "error":
            text = str(info.get("error") or "")[:200]
            reason = classify_venue_error(text)
            with self._lock:
                self._errors.append({"at": now,
                                     "request_id": info.get("request_id"),
                                     "reason": reason, "error": text,
                                     "markets": len(info.get("slugs") or ())})
                del self._errors[:-ERROR_RING]
                for s in info.get("slugs") or ():
                    rec = self._markets.get(s)
                    if rec is not None:
                        # FAIL CLOSED: an error naming this market's request
                        # refuses it, whether or not a book had arrived.
                        rec["refused"] = {"reason": reason, "error": text,
                                          "at": now,
                                          "request_id": info.get("request_id")}
        elif event == "connect_failed":
            with self._lock:
                self._errors.append({"at": now, "request_id": None,
                                     "reason": ("HANDSHAKE_REFUSED"
                                                if info.get("venue_refusal")
                                                else "CONNECT_FAILED"),
                                     "error": info.get("exception"),
                                     "status": info.get("status")})
                del self._errors[:-ERROR_RING]
            if not info.get("venue_refusal"):
                self._set_state(S_RECONNECTING, "connect failed: %s (status %s)"
                                % (info.get("exception"), info.get("status")))
        elif event == "venue_refused":
            with self._lock:
                self._connected = False
                self._venue_refusal = {"at": now, "status": info.get("status"),
                                       "reason": R_VENUE_REFUSED_KEY}
            self._set_state(S_REFUSED_BY_VENUE,
                            "the venue refused the handshake with HTTP %s"
                            % info.get("status"))
        elif event == "gave_up":
            with self._lock:
                self._connected = False
                self._gave_up = {"at": now,
                                 "consecutive_failures":
                                     info.get("consecutive_failures")}
            self._set_state(S_GAVE_UP, "%s consecutive attempts delivered "
                            "nothing" % info.get("consecutive_failures"))

    def _on_heartbeat(self, message=None) -> None:
        with self._lock:
            self._last_life_at = self._clock()

    def _on_book(self, slug, rec) -> None:
        now = self._clock()
        src = ms._parse_ts((rec or {}).get("source_ts"))
        src_epoch = src.timestamp() if src is not None else None
        with self._lock:
            self._last_life_at = now
            m = self._markets.get(slug)
            if m is None or not self._connected:
                return  # a straggler for a pruned market, or a closed socket
            hw = m.get("source_hw")
            if src_epoch is not None and hw is not None and src_epoch < hw:
                # OUT OF ORDER ON ONE CONNECTION. Whatever `transactTime`
                # denotes, it went backwards for this market -- so the order
                # of what we hold is in doubt. Not CURRENT until a later full
                # book reaches the high-water mark again.
                m["gap"] = {"reason": R_SOURCE_CLOCK_REGRESSED, "at": now,
                            "seq": self._conn_seq, "high_water": hw,
                            "received": src_epoch}
                m["regressions"] = m.get("regressions", 0) + 1
                return
            if src_epoch is not None:
                m["source_hw"] = src_epoch if hw is None else max(hw, src_epoch)
            m["book_seq"] = self._conn_seq
            m["book_at"] = now
            m["venue_state"] = (rec or {}).get("state")
            m["books"] = m.get("books", 0) + 1
            # A FULL BOOK ON THIS CONNECTION IS THE RESNAPSHOT: it closes a
            # connection gap from an earlier connection, and a clock gap once
            # the clock is back at the high-water mark (checked above).
            gap = m.get("gap")
            # A book WITHOUT a readable clock cannot show the order restored,
            # so it does not close a clock gap (it still replaces the book).
            if gap and ((gap["reason"] == R_SOURCE_CLOCK_REGRESSED
                         and src_epoch is not None)
                        or (gap["reason"] != R_SOURCE_CLOCK_REGRESSED
                            and gap.get("seq", 0) < self._conn_seq)):
                m["gap"] = None
                m["resnapshots"] = m.get("resnapshots", 0) + 1

    # ── readiness ────────────────────────────────────────────────────

    def readiness(self, slug, *, now=None) -> dict:
        at = float(now if now is not None else self._clock())
        slug = str(slug or "")
        with self._lock:
            st = self.state
            rec = dict(self._markets.get(slug) or {})
            known = slug in self._markets
            conn_seq, connected = self._conn_seq, self._connected
            life = self._last_life_at
            refusal = dict(self._venue_refusal or {})
        out = {"slug": slug, "subscription_state": st,
               "connection_seq": conn_seq, "connected": connected,
               "book_age_s": None, "silence_s": None,
               "bounds": {"max_silence_s": MAX_SILENCE_S,
                          "max_snapshot_age_s": MAX_SNAPSHOT_AGE_S},
               "venue_state": rec.get("venue_state")}
        if life is not None:
            out["silence_s"] = round(at - life, 3)
        if rec.get("book_at") is not None:
            out["book_age_s"] = round(at - rec["book_at"], 3)

        def _is(state, reason, why, **extra):
            return dict(out, state=state, reason=reason, why=why, **extra)

        if st not in RUNNING_STATES:
            return _is(NOT_SUBSCRIBED, R_NOT_RUNNING,
                       "the subscription is %s in this process" % st)
        if not known:
            return _is(NOT_SUBSCRIBED, R_NOT_REQUESTED,
                       "the decision process has not asked for this market")
        if rec.get("refused"):
            r = rec["refused"]
            return _is(REFUSED_BY_VENUE, r["reason"],
                       "the venue answered this market's subscribe request "
                       "with an error: %s" % r.get("error"),
                       venue_error=r.get("error"))
        if st == S_REFUSED_BY_VENUE:
            return _is(REFUSED_BY_VENUE, R_VENUE_REFUSED_KEY,
                       "the venue refused the websocket handshake (HTTP %s); "
                       "nothing on this key can be subscribed"
                       % refusal.get("status"),
                       venue_status=refusal.get("status"))
        if st == S_GAVE_UP:
            return _is(GAP, R_GAVE_UP,
                       "the reconnect bound is spent; no connection is open")
        gap = rec.get("gap")
        if not connected:
            if gap or rec.get("book_seq") is not None:
                return _is(GAP, R_CONNECTION_LOST,
                           "the connection dropped; the book held before it "
                           "is discarded, not aged")
            return _is(SUBSCRIBING, R_AWAITING_CONNECTION,
                       "no connection is open yet")
        if gap and gap["reason"] == R_SOURCE_CLOCK_REGRESSED:
            return _is(GAP, R_SOURCE_CLOCK_REGRESSED,
                       "the venue's clock for this market went backwards on "
                       "this connection; awaiting a full book at or past the "
                       "high-water mark")
        if rec.get("book_seq") != conn_seq:
            if rec.get("sent_seq") == conn_seq:
                return _is(SNAPSHOT_PENDING,
                           R_AWAITING_RESNAPSHOT if gap else
                           R_AWAITING_FIRST_BOOK,
                           "subscribed on this connection; no full book has "
                           "arrived on it yet")
            if gap:
                return _is(GAP, R_CONNECTION_LOST,
                           "reconnected; the resubscribe has not gone out on "
                           "this connection yet, and the book held before "
                           "the drop is discarded, not aged")
            return _is(SUBSCRIBING, R_AWAITING_SEND,
                       "queued; the subscribe has not gone out on this "
                       "connection")
        if out["silence_s"] is None or out["silence_s"] > MAX_SILENCE_S:
            return _is(STALE, R_SILENT,
                       "the connection has not proven itself alive within "
                       "%.0f s -- a dead socket and a quiet market look the "
                       "same" % MAX_SILENCE_S)
        if out["book_age_s"] is None or out["book_age_s"] > MAX_SNAPSHOT_AGE_S:
            return _is(STALE, R_BOOK_OLD,
                       "the last full book for this market arrived more than "
                       "%.0f s ago" % MAX_SNAPSHOT_AGE_S)
        return _is(CURRENT, R_CURRENT,
                   "a full book arrived %.1f s ago on the current connection, "
                   "alive %.1f s ago. THIS IS NOT A TIMING CERTIFICATE: P5 is "
                   "decided in bettor_stream_currency"
                   % (out["book_age_s"], out["silence_s"]),
                   alive_at=life, last_update_at=rec.get("book_at"))

    def digest(self, *, now=None) -> dict:
        at = float(now if now is not None else self._clock())
        with self._lock:
            slugs = list(self._markets)
            stream = self.stream
            base = {
                "version": SUBSCRIPTION_VERSION,
                "subscription_state": self.state,
                "why": self.state_why,
                "state_for_s": round(at - self.state_since, 3),
                "connected": self._connected,
                "connection_seq": self._conn_seq,
                "last_disconnect": dict(self._last_disconnect or {}) or None,
                "venue_refusal": dict(self._venue_refusal or {}) or None,
                "gave_up": dict(self._gave_up or {}) or None,
                "starts": self._starts,
                "restarts": list(self._restarts[-5:]),
                "venue_errors": list(self._errors[-5:]),
            }
        by, why_by = {}, {}
        current, sample = [], []
        for s in slugs:
            r = self.readiness(s, now=at)
            by[r["state"]] = by.get(r["state"], 0) + 1
            why_by[r["reason"]] = why_by.get(r["reason"], 0) + 1
            if r["state"] == CURRENT:
                if len(current) < DIGEST_SAMPLE:
                    current.append(s)
            elif len(sample) < DIGEST_SAMPLE:
                sample.append({"slug": s, "state": r["state"],
                               "reason": r["reason"]})
        sx = {}
        if stream is not None:
            try:
                sx = {"attempts": stream.socket_connect_attempts,
                      "reconnects": stream.reconnects,
                      "consecutive_failures": stream.consecutive_failures,
                      "last_backoffs_s": list(stream.backoff_log[-5:]),
                      "subscribe_messages_sent":
                          stream.subscribe_messages_sent,
                      "updates": stream.updates,
                      "heartbeats": stream.heartbeats,
                      "heartbeat_registered": stream.heartbeat_registered}
            except Exception:  # noqa: BLE001
                sx = {"read_failed": True}
        return dict(base, markets=len(slugs), by_readiness=by,
                    by_reason=why_by, current_sample=current,
                    not_current_sample=sample, stream=sx,
                    policy=dict(self._policy),
                    currency_gate={
                        "feed_level_missing": list(sc.MISSING_PRECONDITIONS),
                        "admits_only_when": (
                            "this market is CURRENT here AND every P1-P6 "
                            "precondition holds in bettor_stream_currency. "
                            "CURRENT alone is not a timing certificate")})


def _new_market(at: float) -> dict:
    return {"wanted_at": at, "requested_at": at, "sent_seq": None,
            "sent_at": None, "request_id": None, "book_seq": None,
            "book_at": None, "source_hw": None, "gap": None,
            "refused": None, "venue_state": None}


# ── the process's one subscription ──────────────────────────────────

_ACTIVE_LOCK = threading.Lock()
_ACTIVE: MarketSubscription | None = None
_LAST_START: dict = {"state": S_NOT_STARTED,
                     "why": "start_default() has not run in this process"}
_M1_REFUSALS: dict = {}


def install(sub: MarketSubscription | None) -> None:
    """Make `sub` the process's subscription (None uninstalls). Tests use
    this; production uses `start_default`."""
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = sub


def active() -> MarketSubscription | None:
    with _ACTIVE_LOCK:
        return _ACTIVE


def reset() -> None:
    """Tests only."""
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = None
        _LAST_START.clear()
        _LAST_START.update(state=S_NOT_STARTED,
                           why="start_default() has not run in this process")
        _M1_REFUSALS.clear()


def enabled() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in ENV_ON


def start_default(*, settings=None) -> dict:
    """Arm the process's subscription from configuration. NEVER RAISES.

    Called once by the entry loop after it holds the writer lock, so only the
    process that decides subscribes. Credentials come from the same settings
    object `pmus._get_client` and the incentive observer read; they are passed
    to the stream constructor and nowhere else.
    """
    try:
        if active() is not None:
            return {"started": False, "state": active().state,
                    "why": "already installed"}
        if not enabled():
            _LAST_START.update(state=S_DISABLED,
                               why="%s is not set to on" % ENV_FLAG)
            return dict(_LAST_START, started=False)
        if settings is None:
            from .config import settings as _settings
            settings = _settings()
        source = credential_source()
        _LAST_START["credential_source"] = source
        if source == KEY_DEDICATED:
            key_id = getattr(settings, "pmus_md_key_id", None)
            secret = getattr(settings, "pmus_md_secret_key", None)
            if not key_id or not secret:
                _LAST_START.update(
                    state=S_WAITING_FOR_DEDICATED_KEY,
                    why=("PMUS_MD_KEY_ID / PMUS_MD_SECRET_KEY are not configured "
                         "in this process; the shared venue key is not used "
                         "unless %s=shared is set explicitly" % ENV_KEY_SOURCE))
                return dict(_LAST_START, started=False)
        else:
            key_id = getattr(settings, "pmus_key_id", None)
            secret = getattr(settings, "pmus_secret_key", None)
        if not key_id or not secret:
            _LAST_START.update(state=S_NO_CREDENTIALS,
                               why="the venue key is not configured in this "
                                   "process; nothing is attempted")
            return dict(_LAST_START, started=False)
        sub = MarketSubscription(key_id, secret)
        install(sub)
        got = sub.start()
        _LAST_START.update(state=sub.state, why="started by start_default")
        return dict(got)
    except Exception as exc:  # noqa: BLE001 -- the loop must not die here
        _LAST_START.update(state=S_STOPPED,
                           why="start_default raised %s" % type(exc).__name__)
        return dict(_LAST_START, started=False)


def shutdown_default(*, wait_s: float = 5.0) -> dict:
    """Clean shutdown of the process's subscription. NEVER RAISES."""
    sub = active()
    if sub is None:
        return {"stopped": False, "why": "nothing installed"}
    try:
        return sub.stop(wait_s=wait_s)
    except Exception as exc:  # noqa: BLE001
        return {"stopped": False, "why": type(exc).__name__}


def want(slugs, *, now=None) -> dict:
    """The decision process needs these markets. NEVER RAISES; a no-op when
    no subscription is running."""
    sub = active()
    if sub is None:
        return {"queued": 0, "why": R_NOT_RUNNING}
    try:
        return sub.want(list(slugs or ()), now=now)
    except Exception as exc:  # noqa: BLE001
        return {"queued": 0, "why": type(exc).__name__}


def readiness(slug, *, now=None) -> dict:
    """ONE MARKET'S READINESS. NEVER RAISES, and never answers CURRENT for a
    reason it cannot show."""
    sub = active()
    if sub is None:
        return {"slug": str(slug or ""), "state": NOT_SUBSCRIBED,
                "reason": R_NOT_RUNNING,
                "subscription_state": _LAST_START.get("state"),
                "why": "no market-data subscription is installed in this "
                       "process (%s)" % _LAST_START.get("why")}
    try:
        return sub.readiness(slug, now=now)
    except Exception as exc:  # noqa: BLE001
        return {"slug": str(slug or ""), "state": NOT_SUBSCRIBED,
                "reason": R_NOT_RUNNING,
                "why": "readiness raised %s" % type(exc).__name__}


def m1_refusal_name(ready: dict | None) -> str | None:
    """Readiness -> the named M1 reason, or None when CURRENT."""
    r = ready or {}
    st = r.get("state")
    if st == CURRENT:
        return None
    if st == NOT_SUBSCRIBED:
        return (M1_MARKET_NOT_SUBSCRIBED if r.get("reason") == R_NOT_REQUESTED
                else M1_SUBSCRIPTION_NOT_RUNNING)
    return M1_REFUSAL_BY_STATE.get(st, M1_SUBSCRIPTION_NOT_RUNNING)


def m1_refusal_detail(slug, *, now=None) -> dict:
    """WHICH PART OF M1 WAS MISSING FOR THIS MARKET, for a refused decision.

    Carried BESIDE VENUE_BOOK_CURRENCY_NOT_ESTABLISHED, never instead of it.
    A venue refusal or entitlement denial is named here; a CURRENT
    subscription on a feed whose timing the venue has not documented is
    named M1_FEED_TIMING_NOT_DOCUMENTED_P5. NEVER RAISES.
    """
    try:
        ready = readiness(slug, now=now)
        name = m1_refusal_name(ready)
        if name is None and sc.MISSING_PRECONDITIONS:
            name = M1_FEED_TIMING_NOT_DOCUMENTED
        elif name is None:
            # CURRENT here and every feed-level precondition met, yet not
            # established at the verdict instant: the currency module's own
            # per-market checks or the evaluator's re-aged bounds refused it.
            name = M1_NOT_ESTABLISHED_AT_THE_VERDICT
        if name is not None:
            with _ACTIVE_LOCK:
                _M1_REFUSALS[name] = _M1_REFUSALS.get(name, 0) + 1
        return {"m1_refusal": name,
                "readiness": {k: ready.get(k) for k in
                              ("state", "reason", "why", "subscription_state",
                               "book_age_s", "silence_s", "venue_error",
                               "venue_status")
                              if ready.get(k) is not None},
                "feed_level_missing": list(sc.MISSING_PRECONDITIONS)}
    except Exception as exc:  # noqa: BLE001
        return {"m1_refusal": M1_SUBSCRIPTION_NOT_RUNNING,
                "why": "m1_refusal_detail raised %s" % type(exc).__name__}


def heartbeat_digest(*, now=None) -> dict:
    """What the loop's heartbeat carries. Bounded; NEVER RAISES."""
    try:
        sub = active()
        with _ACTIVE_LOCK:
            m1 = dict(_M1_REFUSALS)
        if sub is None:
            return {"version": SUBSCRIPTION_VERSION,
                    "subscription_state": _LAST_START.get("state"),
                    "why": _LAST_START.get("why"),
                    "enabled_by": ENV_FLAG, "markets": 0, "by_readiness": {},
                    "credential_source": _LAST_START.get("credential_source"),
                    "decision_m1_refusals_since_start": m1}
        d = sub.digest(now=now)
        d["enabled_by"] = ENV_FLAG
        d["credential_source"] = _LAST_START.get("credential_source")
        d["decision_m1_refusals_since_start"] = m1
        return d
    except Exception as exc:  # noqa: BLE001
        return {"version": SUBSCRIPTION_VERSION,
                "digest_failed": type(exc).__name__}


def describe() -> dict:
    return {
        "subscription": SUBSCRIPTION_VERSION,
        "enabled_by": ENV_FLAG,
        "default": "off",
        "readiness_states": list(READINESS_STATES),
        "submits_orders": False,
        "reconnect_policy": dict(RECONNECT_POLICY),
        "restart_after_gave_up_s": RESTART_AFTER_GAVE_UP_S,
        "restart_after_venue_refusal_s": RESTART_AFTER_VENUE_REFUSAL_S,
        "bounds": {"max_silence_s": MAX_SILENCE_S,
                   "max_snapshot_age_s": MAX_SNAPSHOT_AGE_S,
                   "introduced_here": "none -- both are the currency "
                                      "module's"},
        "gap_detection": (
            "connection epochs (a drop returns every market to "
            "SNAPSHOT_PENDING until a full book arrives on the new "
            "connection) and the venue clock moving backwards on one "
            "connection. The payload has no sequence number"),
        "current_is_not": (
            "a timing certificate. P5 is decided by bettor_stream_currency "
            "and is a property of the venue's published protocol"),
    }
