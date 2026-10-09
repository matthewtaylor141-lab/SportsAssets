"""KALSHI AUTHENTICATED WEBSOCKET ORDER BOOKS -- THE PRIMARY KALSHI BOOK
SOURCE (Kalshi rep production contract 2026-10-07; docs captured under
research/kalshi_canonical_venue/REP_PRODUCTION_CONTRACT_2026-10-07).

  connection   wss://external-api-ws.kalshi.com/trade-api/ws/v2, signed
               handshake: KALSHI-ACCESS-KEY / -SIGNATURE / -TIMESTAMP where
               the signature is RSA-PSS(SHA-256, MGF1-SHA256, salt = digest)
               -- or Ed25519 for an Ed25519 key -- over
               timestamp_ms + "GET" + "/trade-api/ws/v2"
  channel      orderbook_delta (market_tickers): an `orderbook_snapshot`
               first, then `orderbook_delta` updates; every message carries
               the subscription id (sid) and a per-sid sequence number (seq)
  levels       yes_dollars_fp / no_dollars_fp are BIDS ([price, count]);
               a delta is (price_dollars, delta_fp, side). Within one market
               NO ask = 1 - best YES bid and YES ask = 1 - best NO bid: one
               book, one liquidity pool (kalshi_market_data.book_from_
               orderbook reads the same shape)

THE DOCUMENTED COMMAND SET (RC6.2, docs.kalshi.com websocket-connection,
asyncapi, orderbook-updates, changelog 2025-09-25 / 2026-06-18). A repeated
`subscribe` on the channel no longer opens a second subscription: it MERGES
its tickers into the existing sid and is answered by `ok` {id, sid, seq,
msg.market_tickers (the full list after the update)} -- never `subscribed`
-- and re-subscribing tickers the sid already holds is "no action" (no
snapshot comes back). So, per connection:
  * ONE `subscribe` (its first chunk of markets); the venue's `subscribed`
    {id: that command} names the sid. Every further market is added with
    `update_subscription` add_markets {sid}, removed with delete_markets
    {sid}; each chunk is bound to the sid when it is SENT. The `ok` of an
    add / delete / get_snapshot carries the venue's full ticker list after
    that command: a ticker we expect there but the venue lacks is GAP
    (R_NOT_HELD_BY_VENUE) and added again ONCE.
  * A sequence gap never tears the subscription down (RC6.1 unsubscribed the
    sid and subscribed again; the merge answered `ok`, the next snapshot
    looked like a gap, and every snapshot on the "dead" sid subscribed its
    market again -- the storm that held the market plane's event loop). The
    sid's books go GAP, the gap frame's seq (the highest seen) is the new
    baseline, and ONE update_subscription get_snapshot {sid, its tickers}
    asks for fresh snapshots on the SAME sid; a book is CURRENT again only
    from a snapshot in sequence after that baseline. A second gap while the
    get_snapshot is outstanding ends the session (GapDuringRecovery); the
    run loop reconnects after its backoff.
  * Only errors 10 (channel error) and 25 (subscription buffer overflow)
    end a subscription ("the user must resubscribe"): its books GAP, the
    session ends and the reconnect subscribes afresh. Every other error is
    counted; one carrying (sid, seq) is a control frame of the sequence. An
    `unsubscribed` naming a sid we hold (we never send `unsubscribe`, so the
    venue did) GAPs that sid's books (R_UNSUBSCRIBED_BY_VENUE) and ends the
    session.
  * No command is ever sent in reaction to a frame on a sid that is not
    the session's live subscription (a dead sid, or a number first seen
    after our subscription was acknowledged): it is counted and ignored.
  * Hard per-session bounds (fail closed, the session ends, named in the
    heartbeat): commands per minute (MAX_COMMANDS_PER_MINUTE, far below the
    venue's 10k/s), get_snapshot requests per market
    (MAX_SNAPSHOT_REQUESTS_PER_MARKET), and -- the RC6 plane-hang bound, kept
    as defence in depth -- a market asked for a snapshot more than
    MAX_RESUBSCRIBES_WITHOUT_RECOVERY times without becoming CURRENT.
  * The session yields to the event loop every YIELD_EVERY_FRAMES frames
    and after each command burst: a burst of frames never holds the plane's
    shared loop.

THE SEQUENCE RULE FOR CONTROL FRAMES (chosen here; the docs give `ok`,
`unsubscribed` and scoped `error` the same `seq` field as the data, "used
for snapshot/delta consistency", but never say in words whether it is one
counter). Every frame carrying our (sid, seq) is part of that sid's
sequence (`SHARED`, the plain reading): seq == last + 1 advances, anything
else is a gap. The guard for the other reading: a control frame may advance
the sequence past CURRENT books only when its seq is above what a separate
control counter could have reached by then -- the reply to our j-th update
command on the sid can carry at most j + CONTROL_SEQ_SLACK from a separate
counter (replies come in command order; the subscribe's own reply may count
too). At or below that, with a book CURRENT on the sid, it is a gap
(R_CONTROL_SEQUENCE_AMBIGUOUS); with no CURRENT book on the sid nothing
can be hidden in the slot, and it advances. A control frame whose seq is at
or below the last seq, or a data frame that continues the DATA frames
exactly over the control frames taken between them, proves a separate
counter: the sid switches to `SEPARATE` (control frames are then counted,
never sequenced; data frames run by themselves).
Why it is safe either way. Shared counter: a control frame is the next
slot, or (after a loss) above it -- a gap, as for data. Separate counter: a
control frame can take a slot only while no book of the sid is CURRENT
(nothing to hide; the data frames it may stand in for precede every
snapshot applied after it, and a snapshot is a whole book) or when its seq
is beyond any separate counter (impossible) -- even when control frames
themselves are lost. What it costs: under a shared counter, a false gap
only when a sid has carried no more frames than update commands (a
one-market quiet subscription: one gap, one get_snapshot, then the counts
part); under a separate counter, none in the usual shape (the first `ok`
after any data is at or below the last seq: SEPARATE, no gap), at most one
gap and one get_snapshot otherwise; never a storm.

SNAPSHOTS ARE IN THE SEQUENCE TOO (RC6 red-team, replay). Kalshi: seq is
"used for snapshot/delta consistency". A replayed or out-of-order snapshot
(seq <= last) or one that skipped a lost message gaps the sid and is not
applied (R_SNAPSHOT_OUT_OF_SEQUENCE / R_SEQ_GAP).

SIDS NEVER ACKNOWLEDGED AS OURS (no `subscribed` answering our subscribe;
legacy rules, unchanged from RC6). Where the sequence starts is unknown: a
delta before the first snapshot is not applied and does not start it, that
snapshot must follow the HIGHEST such seq (+ 1) or -- assumption S5, pinned
by red-team scenario 5 -- carry seq 1; with nothing before it, the first
snapshot starts the sequence at its own seq (assumption S0 when that seq is
above 1: it is taken as the subscription's state then). A gap on such a sid
makes it DEAD until the venue announces it again; no command is ever sent
for it (the runtime cannot name it). The proof
(tests/test_rc6_kalshi_ws_acceptance_model.py) names and counts S0 and S5.

Account limits: GET /trade-api/v2/account/limits is the authoritative
usage tier and read / write token buckets; REST recovery pacing derives
from the read bucket. The schema returns no WebSocket connection cap, so
none is assumed (this runtime opens ONE connection).

STRUCTURALLY READ-ONLY: this module and its runtime import no order,
cancel, funding, Small Live or capital code (tests/test_kalshi_ws_market_
data.py checks the import closure). The credential is used only to sign
the handshake and the limits read. That is OUR code's boundary, not the
key's: Kalshi documents no read-only key class, so the key itself is
account-wide (market_plane_guard is the process-level wall). The socket
carries `subscribe` and `update_subscription` only (Commands).

THE KEY (RC5, 2026-10-08): loaded and signed by kalshi_key, the one
implementation every Kalshi signer shares -- the value EXACTLY as
configured (production's Ed25519 PEM is 119 characters with its trailing
newline; it used to be .strip()ped here before loading), the type read from
the parsed key, Ed25519 or RSA-PSS by that type.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from decimal import Decimal

from . import kalshi_key as KK

log = logging.getLogger(__name__)

VERSION = "KALSHI_WS_ORDERBOOK_V1"
WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
REST_BASE = "https://external-api.kalshi.com"
LIMITS_PATH = "/trade-api/v2/account/limits"
CHANNEL = "orderbook_delta"
KEY_ID_ENV = "KALSHI_API_KEY_ID"
PRIVATE_KEY_PEM_ENV = "KALSHI_PRIVATE_KEY_PEM"
BOOK_BASIS = "KALSHI_WS_ORDERBOOK_DELTA_SNAPSHOT_THEN_SEQ"
DEFAULT_TOKEN_COST = 10
#: used ONLY when the account-limits read is unavailable: Basic tier read
#: budget (200 tokens/s) at the default cost, halved; labelled as such
FALLBACK_REST_RATE_PER_S = 10.0
#: markets per subscribe / add_markets / get_snapshot command
SUBSCRIBE_CHUNK = 100
#: commands awaiting the venue's reply (`subscribed` / `ok`), kept at most
MAX_PENDING_SUBSCRIBES = 512
#: a session with nothing to read re-reads the wanted set this often (a
#: connection with no subscription receives nothing at all)
IDLE_RECHECK_S = 5.0
#: (RC6 plane hang, 2026-10-09) snapshot requests (get_snapshot, or the one
#: re-add) of ONE market since a snapshot last made its book CURRENT, at
#: most, in one session; one past it ends the session (ResubscribeStorm).
#: Defence in depth: the RC6.2 rules (one get_snapshot per gap, a second gap
#: while it is outstanding ends the session, one re-add) stop first.
MAX_RESUBSCRIBES_WITHOUT_RECOVERY = 2
#: (RC6.2) commands sent in any 60 s of one session, at most (the venue
#: enforces 10k/s; ONE subscribe and a few add / delete / get_snapshot
#: chunks per minute is the steady state); one past it ends the session
MAX_COMMANDS_PER_MINUTE = 300
#: (RC6.2) get_snapshot requests naming one market in one session, at most
MAX_SNAPSHOT_REQUESTS_PER_MARKET = 8
#: (RC6.2) the session loop yields to the event loop at least this often
YIELD_EVERY_FRAMES = 200
#: (RC6.2) an unchanged wanted list is re-read in full this often (frames)
WANTED_RECHECK_FRAMES = 200
#: (RC6.2) a separate control counter cannot carry more than j plus this on
#: the reply to our j-th update command on a sid (the subscribe's own reply
#: may count); a control frame above it can only be a shared slot
CONTROL_SEQ_SLACK = 1
#: (RC6.2) command ids remembered per sid for that bound, at most
MAX_CMD_INDEX = 1024
#: (RC6.2) errors that END a subscription ("the user must resubscribe"):
#: 10 channel error, 25 subscription buffer overflow
TERMINAL_ERROR_CODES = frozenset((10, 25))
#: (RC6.2) bounded diagnostics: the first DIAG_CONNECTIONS connections of a
#: process log at most DIAG_MAX_CONTROL control frames and DIAG_MAX_COMMANDS
#: commands each (KWS_CTRL / KWS_NEXT / KWS_CMD; no tickers, no book data)
DIAG_CONNECTIONS = 3
DIAG_MAX_CONTROL = 200
DIAG_MAX_COMMANDS = 200

CURRENT = "CURRENT"
GAP = "GAP"
PENDING = "AWAITING_SNAPSHOT"
UNSUBSCRIBED = "UNSUBSCRIBED"

#: control-frame sequence modes of one acknowledged subscription
SHARED = "SHARED"
SEPARATE = "SEPARATE"

R_SEQ_GAP = "KALSHI_WS_SEQUENCE_GAP"
R_DISCONNECT = "KALSHI_WS_DISCONNECTED"
R_SUB_ERROR = "KALSHI_WS_SUBSCRIPTION_ERROR"
R_NO_CREDENTIAL = "KALSHI_WS_CREDENTIAL_NOT_PROVISIONED"
#: a snapshot whose seq is not last + 1 on an in-sequence sid (a replayed /
#: out-of-order snapshot, or one that skipped a lost message)
R_SNAPSHOT_OUT_OF_SEQUENCE = "KALSHI_WS_SNAPSHOT_OUT_OF_SEQUENCE"
#: the session's end when a market is asked for a snapshot past
#: MAX_RESUBSCRIBES_WITHOUT_RECOVERY without becoming CURRENT
R_RESUBSCRIBE_STORM = "KALSHI_WS_RESUBSCRIBE_STORM"
#: (RC6.2) a book GAP because the venue ended our subscription by
#: `unsubscribed` (we never send `unsubscribe`)
R_UNSUBSCRIBED_BY_VENUE = "KALSHI_WS_UNSUBSCRIBED_BY_VENUE"
#: (RC6.2) a book GAP because the venue's `ok` lists the subscription's
#: tickers without it
R_NOT_HELD_BY_VENUE = "KALSHI_WS_MARKET_NOT_HELD_BY_VENUE"
#: (RC6.2) a book GAP because a control frame took the next seq while books
#: were CURRENT and a separate control counter could have reached it
R_CONTROL_SEQUENCE_AMBIGUOUS = "KALSHI_WS_CONTROL_SEQUENCE_AMBIGUOUS"
#: (RC6.2) session ends (the run loop reconnects after its backoff)
R_GAP_DURING_RECOVERY = "KALSHI_WS_GAP_DURING_RECOVERY"
R_COMMAND_RATE = "KALSHI_WS_COMMAND_RATE_BOUND"
R_SNAPSHOT_REQUEST_BOUND = "KALSHI_WS_SNAPSHOT_REQUEST_BOUND"
R_SUBSCRIPTION_ENDED = "KALSHI_WS_SUBSCRIPTION_ENDED"
R_SUBSCRIBE_REFUSED = "KALSHI_WS_SUBSCRIBE_REFUSED"


# ── signing (the handshake and the limits read) ─────────────────────────

def load_private_key(pem):
    """Either documented Kalshi key type (Ed25519 or RSA) from the value
    AS CONFIGURED -- never stripped first; a paste repair (base64 of the
    PEM, escaped newlines) only when the value does not load as given.
    ValueError(R_NO_CREDENTIAL) when there is nothing; otherwise a
    kalshi_key.KeyRefused (a ValueError) naming why it is not a key."""
    if pem is None or not str(pem).strip():
        raise ValueError(R_NO_CREDENTIAL)
    return KK.load_private_key(pem)


def signing_message(ts_ms: str, method: str, path: str) -> bytes:
    """timestamp + METHOD + path without the query (kalshi_venue's rule)."""
    return (str(ts_ms) + str(method).upper() + str(path).split("?")[0]
            ).encode("utf-8")


def sign(private_key, ts_ms: str, method: str, path: str) -> str:
    """Ed25519 or RSA-PSS by the key's own type (kalshi_key.sign); any
    other key type is refused by name before anything is signed (it used
    to fall through to the RSA call and fail with a TypeError)."""
    return KK.sign(private_key, signing_message(ts_ms, method, path))


def auth_headers(key_id: str, private_key, method: str, path: str, *,
                 ts_ms: str | None = None) -> dict:
    ts = ts_ms or str(int(time.time() * 1000))
    return {"KALSHI-ACCESS-KEY": str(key_id),
            "KALSHI-ACCESS-SIGNATURE": sign(private_key, ts, method, path),
            "KALSHI-ACCESS-TIMESTAMP": ts}


def credential_present(env) -> bool:
    return bool(str(env.get(KEY_ID_ENV) or "").strip()
                and str(env.get(PRIVATE_KEY_PEM_ENV) or "").strip())


# ── account limits ──────────────────────────────────────────────────────

def parse_limits(body: dict, *, as_of: float) -> dict:
    """GET /account/limits -> the recorded readback. Nothing assumed: a
    field the venue does not return is NOT_RETURNED_BY_VENUE."""
    b = body or {}

    def bucket(x):
        x = x or {}
        return {"refill_rate": x.get("refill_rate"),
                "bucket_capacity": x.get("bucket_capacity")}
    return {"usage_tier": b.get("usage_tier"),
            "read": bucket(b.get("read")), "write": bucket(b.get("write")),
            "grants": list(b.get("grants") or []),
            "websocket_connection_limit": (
                b.get("websocket_connection_limit")
                or "NOT_RETURNED_BY_VENUE"),
            "as_of": as_of, "source": "GET " + LIMITS_PATH}


def pacing(limits: dict | None, *, cost: int = DEFAULT_TOKEN_COST) -> dict:
    """REST recovery requests per second from the ACTUAL read bucket; the
    labelled fallback only when the limits read is unavailable."""
    rr = ((limits or {}).get("read") or {}).get("refill_rate")
    try:
        rate = float(rr) / float(cost)
    except (TypeError, ValueError, ZeroDivisionError):
        rate = None
    if rate and rate > 0:
        return {"rest_requests_per_s": rate,
                "basis": "ACCOUNT_LIMITS_READ_BUCKET (refill %s / cost %d)"
                % (rr, cost)}
    return {"rest_requests_per_s": FALLBACK_REST_RATE_PER_S,
            "basis": "FALLBACK_NO_ACCOUNT_LIMITS_READ (not our account "
                     "limit; conservative public pacing)"}


# ── the books ────────────────────────────────────────────────────────────

SNAP, DELTA = "orderbook_snapshot", "orderbook_delta"


def _d(x) -> Decimal:
    return Decimal(str(x))


class WsBooks:
    """Per-market books under per-sid sequence discipline.

    THE SEQUENCE IS THE SUBSCRIPTION'S, NOT THE BOOK'S (RC6, 2026-10-09).
    The venue numbers every message of a sid -- each market's
    `orderbook_snapshot` as well as every `orderbook_delta` (and, RC6.2, the
    `ok` / `unsubscribed` / scoped `error` frames it sends on the sid) --
    with one sequence ("Used for snapshot/delta consistency"). So every
    in-sequence message advances the sid, a skip or a replay is a gap of the
    whole sid, and a gap or an error on a sid gaps only the books that live
    on that sid; a book already CURRENT on another live sid is left alone.

    TWO KINDS OF SID.
      * ACKNOWLEDGED (`bind(sid, tickers, ours=True)`: the venue's
        `subscribed` answered OUR subscribe): RC6.2 rules (module docstring).
        The sequence starts at the ack (the first message is seq 1). A gap
        GAPs the sid's books, re-bases the sid on the highest seq seen and
        names the books to ask a fresh snapshot for (`recover[sid]`); the sid
        stays live and the venue's snapshots on it -- the in-flight ones and
        get_snapshot's -- restore CURRENT one by one. Only an `error` 10 / 25
        or an `unsubscribed` ends it (`fatal` tells the subscriber to end
        the session). A second gap while a requested snapshot is outstanding
        sets `fatal` too.
      * NEVER ACKNOWLEDGED AS OURS (frames without an ack of ours, or after
        an announcement answering none of our commands; the RC6 rules,
        unchanged): DEAD after a gap until the venue announces the number
        again; its markets are named in `resubscribe` (for the record: no
        command is ever sent for such a sid).
    A sid number first seen AFTER our subscription was acknowledged on this
    connection is not ours and never was: its frames are counted
    (`ignored_unknown_sid`) and ignored.

    `bind(sid, tickers)` (the subscriber: on the ack, and for each
    add_markets chunk when it is sent) records which markets a sid carries
    before any snapshot arrives."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.books: dict = {}       # ticker -> {yes, no, sid, state, ...}
        self.sid_seq: dict = {}     # sid -> last seq (None: dead)
        self.sid_markets: dict = {}  # sid -> set(tickers)
        self.ticker_sid: dict = {}
        self.dead_sids: set = set()
        self.stats = {"snapshots": 0, "deltas": 0, "gaps": 0,
                      "disconnects": 0, "ignored_after_gap": 0,
                      "errors": 0, "ignored_dead_sid": 0,
                      "snapshots_out_of_sequence": 0, "forgotten": 0,
                      "ignored_not_tracked": 0,
                      # (RC6.2)
                      "ignored_unknown_sid": 0, "errors_terminal": 0,
                      "errors_nonterminal": 0, "control_frames": 0,
                      "control_consumed": 0, "control_ignored": 0,
                      "control_seq_separate": 0, "unsubscribed_by_venue": 0,
                      "not_held_by_venue": 0}
        self.resubscribe: set = set()
        #: (RC6 acceptance model) sid -> the HIGHEST seq of a delta seen on
        #: a sid not acknowledged as ours, before its first snapshot: not its
        #: sequence, but that snapshot must agree with it
        #: (_first_snapshot_after)
        self.sid_pre: dict = {}
        #: (RC6 acceptance model) bumped on every change to a book's levels
        #: or state; each book carries the value of its latest change
        #: ("rev"), so a writer can tell any two versions of a book apart
        #: (the worker's flush key)
        self.rev = 0
        self.connected = False
        #: (RC6) markets the runtime stopped tracking in THIS session (see
        #: `forget`); cleared with the session
        self.forgotten: set = set()
        #: (RC6.2) sid -> the state of a subscription acknowledged as ours:
        #: mode (SHARED / SEPARATE control-frame sequence), last_data (the
        #: last DATA seq applied), ctrl_since_data (control frames taken into
        #: the sequence since it), n_cmds (update commands sent on it),
        #: cmd_index (command id -> its 1-based index among them), ended
        #: (None, or why it ended), awaiting (markets whose requested
        #: snapshot is outstanding)
        self.anchors: dict = {}
        #: (RC6.2) sid numbers seen before any ack of ours on this connection
        self.legacy_sids: set = set()
        #: (RC6.2) sid -> markets of an acknowledged sid to ask a fresh
        #: snapshot for (the subscriber sends get_snapshot and clears it)
        self.recover: dict = {}
        #: (RC6.2) markets whose book left CURRENT since the subscriber last
        #: drained this (the worker writes their rows unreadable at once)
        self.left_current: set = set()
        #: (RC6.2) a reason the subscriber must end the session for
        #: (R_GAP_DURING_RECOVERY, R_SUBSCRIPTION_ENDED,
        #: R_SUBSCRIBE_REFUSED); WsBooks alone only records it
        self.fatal = None

    def _book(self, t):
        return self.books.setdefault(t, {
            "yes": {}, "no": {}, "sid": None, "state": PENDING,
            "snapshot_at": None, "updated_at": None, "venue_ts_ms": None,
            "seq": None, "why": None, "rev": 0})

    def _touch(self, t, b, **change) -> None:
        """Apply a change to a book's levels or state, and stamp it; a book
        leaving CURRENT is noted for the immediate unreadable write."""
        if b["state"] == CURRENT and change.get("state", CURRENT) != CURRENT:
            self.left_current.add(t)
        self.rev += 1
        b.update(change, rev=self.rev)

    def want(self, tickers) -> None:
        for t in tickers:
            self.forgotten.discard(t)
            self._book(t)

    def forget(self, tickers) -> dict:
        """(RC6) Stop holding the books of markets no longer tracked:
        {sid: [tickers]} of the live subscriptions they were on.

        WHY. Nothing removed a book: the runtime's wanted set rolls (the
        fixtures starting within [now - 4 h, now + 36 h]) but every market
        it ever wanted kept its book -- and counted in its freshness, as
        CURRENT -- for the life of the process. Production 2026-10-09 03:11Z
        (research-sql rc6_api-responsive_kalshi_ws_growth.sql): the
        heartbeat held 235 books, 235/235 "current", while the wanted set
        was 185; 50 of the CURRENT books were markets no longer tracked.
        A message still in flight for a forgotten market keeps its sid's
        sequence (an unbroken seq stays unbroken, a jump is still a gap)
        and never brings the book back (on_message). (RC6.2) The subscriber
        also takes the market out of the venue's subscription
        (delete_markets)."""
        out: dict = {}
        for t in tickers:
            if t not in self.books:
                continue
            self.books.pop(t, None)
            sid = self.ticker_sid.pop(t, None)
            if sid is not None:
                out.setdefault(sid, []).append(t)
            # bound (acknowledged) on any sid, live or dead: a later gap of
            # that sid must not bring the book back
            for ms in self.sid_markets.values():
                ms.discard(t)
            for a in self.anchors.values():
                a["awaiting"].discard(t)
            for ms in self.recover.values():
                ms.discard(t)
            self.resubscribe.discard(t)
            self.left_current.discard(t)
            self.forgotten.add(t)
            self.stats["forgotten"] += 1
        return {k: sorted(v) for k, v in out.items()}

    # ── which sid a frame is on ──
    def _unknown(self, sid) -> bool:
        """(RC6.2) A number first seen after our subscription was
        acknowledged on this connection: not ours, and never was."""
        return bool(self.anchors) and sid not in self.anchors \
            and sid not in self.legacy_sids

    def _seen(self, sid) -> None:
        if sid is not None and not self.anchors:
            self.legacy_sids.add(sid)

    def _not_tracked(self, typ, sid, seq) -> str:
        """A message for a forgotten market on a sid never acknowledged as
        ours: no book, but its sid's sequence is kept exactly as a tracked
        market's message keeps it -- a snapshot too: one past a lost
        message, or a replay (seq <= last), gaps the sid like any snapshot
        out of sequence (review of the three-lane merge b4506ed9: re-basing
        the sid on it served a tracked book missing the lost update as
        CURRENT, and a replay re-applied a delta). On a DEAD sid (its
        sequence already broke) nothing is re-armed and nothing is
        resubscribed: its place in the sequence cannot be known until the
        venue announces the sid anew, and the market is no longer wanted."""
        if sid in self.dead_sids:
            self.stats["ignored_not_tracked"] += 1
            return "IGNORED_DEAD_SID"
        last = self.sid_seq.get(sid)
        if last is None:
            if typ != SNAP:
                self._pre_snapshot(sid, seq)
                self.stats["ignored_not_tracked"] += 1
                return "IGNORED_NOT_TRACKED"
            last = self._first_snapshot_after(sid, seq)
        if last is not None and seq != last + 1:
            if typ == SNAP:
                self.stats["snapshots_out_of_sequence"] += 1
                self._gap_sid(sid, R_SNAPSHOT_OUT_OF_SEQUENCE
                              if seq <= last else R_SEQ_GAP)
            else:
                self._gap_sid(sid, R_SEQ_GAP)
            return "GAP"
        self.stats["ignored_not_tracked"] += 1
        self.sid_seq[sid] = seq
        self.sid_pre.pop(sid, None)
        return "IGNORED_NOT_TRACKED"

    def _pre_snapshot(self, sid, seq) -> None:
        """A delta on a sid with no sequence yet (never acknowledged as
        ours, no snapshot): not applied, not its sequence; the HIGHEST such
        seq is kept -- a replayed older delta after a newer one used to
        lower the mark, so a snapshot older than a delta already received
        passed as its successor (RC6 acceptance model, F3)."""
        pre = self.sid_pre.get(sid)
        self.sid_pre[sid] = seq if pre is None else max(pre, seq)

    def _first_snapshot_after(self, sid, seq):
        """What the FIRST snapshot on a sid with no sequence is checked
        against (None: it starts the sequence). Only a sid never
        acknowledged as ours gets here (`bind` sequences ours from the ack).
        A delta before that snapshot is not applied and does not start the
        sequence -- a delta numbered from an older subscription is never
        applied (red-team scenario 5) -- but the snapshot must follow the
        highest such seq (+ 1). Assumption S5 (red-team scenario 5 pins it):
        a seq-1 snapshot starts a subscription whatever preceded it on a sid
        this connection never acknowledged as ours. Any other -- a replay or
        duplicate of what preceded it, or one past a lost message -- is out
        of sequence like any snapshot. With nothing before it, the snapshot
        starts the sequence at its own seq (assumption S0 when that is
        above 1)."""
        pre = self.sid_pre.get(sid)
        return None if pre is None or seq == 1 else pre

    def on_connected(self) -> None:
        self.connected = True

    def on_disconnected(self) -> None:
        """Every book GAP: a book held across a reconnect is never reused.
        (The next connection subscribes every wanted market afresh; the
        resubscribe set is the subscriber's to reset then.)"""
        self.connected = False
        self.stats["disconnects"] += 1
        for t, b in self.books.items():
            if b["state"] != UNSUBSCRIBED:
                self._touch(t, b, state=GAP, why=R_DISCONNECT, sid=None)
                self.resubscribe.add(t)
        self.sid_seq.clear()
        self.sid_pre.clear()
        self.sid_markets.clear()
        self.ticker_sid.clear()
        self.dead_sids.clear()
        self.anchors.clear()
        self.legacy_sids.clear()
        self.recover.clear()
        self.fatal = None
        # a new session subscribes only what is wanted
        self.forgotten.clear()

    def bind(self, sid, tickers, *, ours: bool = True) -> None:
        """The venue acknowledged a subscribe as `sid` (or, RC6.2, an
        add_markets / get_snapshot chunk is being sent on it): the markets
        it carries are known before their snapshots arrive. A market
        CURRENT on another sid keeps that sid.

        `ours`: the ack answers the subscribe command this session sent
        (its id). Then the subscription's messages follow it in order from
        seq 1, so the sid is sequenced from here (last seq 0): a first
        message that is not seq 1 -- a lost or late first snapshot, a delta
        ahead of it -- gaps the sid like any break (RC6 acceptance model,
        F1). A live acknowledged sid is never reset by a further bind (a
        chunk sent on it); a number whose acknowledged subscription ended
        starts a NEW subscription (a reused number: nothing it carried
        before is this subscription's). A sid that already had a sequence
        before our ack keeps it; on one with only unsequenced deltas, those
        preceded the ack and are not this subscription's.

        Not `ours`: an announcement answering none of our commands. It
        revives a dead number never acknowledged as ours (RC6 rule); it
        never touches an acknowledged sid or a number first seen after our
        ack."""
        if sid is None:
            return
        if ours:
            a = self.anchors.get(sid)
            if a is None or a["ended"]:
                if a is not None:
                    # an ended subscription's number, reused for ours
                    self.dead_sids.discard(sid)
                    self.sid_seq.pop(sid, None)
                    self.sid_markets.pop(sid, None)
                    for t, s in list(self.ticker_sid.items()):
                        if s == sid:
                            self.ticker_sid.pop(t, None)
                else:
                    self._revive(sid)
                prev = self.sid_seq.get(sid)
                if prev is None:
                    self.sid_seq[sid] = prev = 0
                    self.sid_pre.pop(sid, None)
                self.anchors[sid] = {"mode": SHARED, "last_data": prev,
                                     "ctrl_since_data": 0, "n_cmds": 0,
                                     "cmd_index": {},
                                     "ended": None, "awaiting": set()}
                self.recover.pop(sid, None)
        else:
            if sid in self.anchors or self._unknown(sid):
                return
            self._seen(sid)
            self._revive(sid)
        # a market dropped while this subscribe awaited its ack is not
        # brought back by the ack (review of b4506ed9)
        tickers = [t for t in tickers if t not in self.forgotten]
        self.sid_markets.setdefault(sid, set()).update(tickers)
        for t in tickers:
            b = self._book(t)
            if b["state"] != CURRENT or b["sid"] is None:
                self.ticker_sid[t] = sid

    def _revive(self, sid) -> None:
        """The venue announced (`subscribed`) a sid number this connection
        already saw die -- the docs promise no unique sid: a NEW
        subscription under a reused number. One connection delivers in
        order, so every message of the old subscription preceded the
        announcement: its sequence and markets start afresh (left dead, the
        new subscription's snapshots were ignored until a reconnect). A
        live sid is never reset by an announcement. (RC6.2: only for a sid
        never acknowledged as ours.)"""
        if sid in self.dead_sids and sid not in self.anchors:
            self.dead_sids.discard(sid)
            self.sid_seq.pop(sid, None)
            self.sid_pre.pop(sid, None)
            self.sid_markets.pop(sid, None)

    def _current_elsewhere(self, b, sid) -> bool:
        return b["state"] == CURRENT and b["sid"] is not None \
            and b["sid"] != sid

    def _held_elsewhere(self, t, b, sid) -> bool:
        """The market is served -- CURRENT -- or awaited -- acknowledged,
        its snapshot still to come -- on a LIVE sid other than `sid`: that
        subscription delivers its book, so nothing on `sid` resubscribes
        it (a resubscribe would open a second live sid for it)."""
        if self._current_elsewhere(b, sid):
            return True
        s = self.ticker_sid.get(t)
        return s is not None and s != sid and s not in self.dead_sids

    def _gap_sid(self, sid, why) -> None:
        """A sid never acknowledged as ours broke: DEAD (RC6 rule)."""
        if sid in self.dead_sids:
            return
        self.stats["gaps"] += 1
        for t in self.sid_markets.get(sid, set()):
            b = self._book(t)
            if self._held_elsewhere(t, b, sid):
                continue
            self._touch(t, b, state=GAP, why=why)
            self.resubscribe.add(t)
        self.sid_seq[sid] = None
        self.dead_sids.add(sid)

    # ── an acknowledged subscription (RC6.2) ──
    def _current_on(self, sid) -> bool:
        for t in self.sid_markets.get(sid, ()):
            b = self.books.get(t)
            if b is not None and b["state"] == CURRENT and b["sid"] == sid:
                return True
        return False

    def _gap_acked(self, a, sid, why, seq, *, data: bool) -> None:
        """A gap of an acknowledged sid: its books GAP (one already CURRENT
        on another live sid excepted), the sid re-based on the highest seq
        seen (the gap frame's when it skipped ahead; a replay or duplicate
        never lowers it), every market bound to it named for a fresh
        snapshot. The sid stays live. A gap while a requested snapshot is
        still outstanding asks the subscriber to end the session."""
        if a["awaiting"]:
            self.fatal = self.fatal or R_GAP_DURING_RECOVERY
        self.stats["gaps"] += 1
        last = self.sid_seq.get(sid) or 0
        if data:
            a["last_data"] = max(a["last_data"], seq)
        if a["mode"] == SEPARATE:
            self.sid_seq[sid] = a["last_data"]
        else:
            self.sid_seq[sid] = max(last, seq)
        a["ctrl_since_data"] = 0
        need = self.recover.setdefault(sid, set())
        for t in sorted(self.sid_markets.get(sid, ())):
            b = self.books.get(t)
            if b is None or t in self.forgotten or \
                    self._held_elsewhere(t, b, sid):
                continue
            if b["state"] == CURRENT:
                self._touch(t, b, state=GAP, why=why)
            need.add(t)
            self.resubscribe.add(t)

    def _end(self, sid, why) -> None:
        """An acknowledged subscription ENDED (error 10 / 25, or the
        venue's `unsubscribed`): its books GAP, the number dead, nothing
        outstanding on it; the subscriber ends the session."""
        a = self.anchors[sid]
        if a["ended"]:
            return
        a["ended"] = why
        a["awaiting"].clear()
        self.recover.pop(sid, None)
        for t in sorted(self.sid_markets.get(sid, ())):
            b = self.books.get(t)
            if b is None or self._current_elsewhere(b, sid):
                continue
            self._touch(t, b, state=GAP, why=why)
            self.resubscribe.add(t)
        self.sid_seq[sid] = None
        self.dead_sids.add(sid)
        self.fatal = self.fatal or R_SUBSCRIPTION_ENDED

    def _control_seq(self, a, sid, seq, cid=None) -> str:
        """Where a control frame's seq sits in an acknowledged sid's
        sequence (module docstring, THE SEQUENCE RULE FOR CONTROL FRAMES).
        `cid`: the command it answers (its index among our update commands
        on the sid bounds a separate counter; unknown: every command sent)."""
        if seq is None:
            return "NO_SEQ"
        if a["mode"] == SEPARATE:
            self.stats["control_ignored"] += 1
            return "IGNORED"
        last = self.sid_seq.get(sid) or 0
        if seq == last + 1:
            j = a["cmd_index"].get(cid, a["n_cmds"]) \
                if type(cid) is int else a["n_cmds"]
            if seq > j + CONTROL_SEQ_SLACK or not self._current_on(sid):
                self.sid_seq[sid] = seq
                a["ctrl_since_data"] += 1
                self.stats["control_consumed"] += 1
                return "CONSUMED"
            return "AMBIGUOUS"
        if seq <= last:
            # a shared counter never repeats a seq: a separate one does
            a["mode"] = SEPARATE
            self.sid_seq[sid] = a["last_data"]
            self.stats["control_seq_separate"] += 1
            self.stats["control_ignored"] += 1
            return "SEPARATE"
        return "GAP"

    def _data_seq(self, a, sid, seq) -> bool:
        """Is a data frame the acknowledged sid's next? (advances it)"""
        last = self.sid_seq.get(sid) or 0
        if a["mode"] == SEPARATE:
            ok = seq == a["last_data"] + 1
        elif seq == last + 1:
            ok = True
        elif (seq == a["last_data"] + 1 and a["ctrl_since_data"] >= 1
              and last == a["last_data"] + a["ctrl_since_data"]):
            # the data run continues exactly over the control frames just
            # taken into the sequence: their seq is a separate counter's
            a["mode"] = SEPARATE
            self.stats["control_seq_separate"] += 1
            ok = True
        else:
            ok = False
        if ok:
            a["last_data"] = seq
            a["ctrl_since_data"] = 0
            self.sid_seq[sid] = seq
        return ok

    def _control(self, m, typ, sid) -> str:
        self.stats["control_frames"] += 1
        code = (m.get("msg") or {}).get("code") if typ == "error" else None
        if typ == "error":
            self.stats["errors"] += 1
            self.stats["errors_terminal" if code in TERMINAL_ERROR_CODES
                       else "errors_nonterminal"] += 1
        a = self.anchors.get(sid) if sid is not None else None
        if a is None:
            if sid is not None and self._unknown(sid):
                self.stats["ignored_unknown_sid"] += 1
            elif sid is not None and sid not in self.dead_sids and (
                    sid in self.sid_seq or sid in self.sid_markets):
                # a sid never acknowledged as ours: only what ends a
                # subscription ends it
                if typ == "error" and code in TERMINAL_ERROR_CODES:
                    self._gap_sid(sid, R_SUB_ERROR)
                elif typ == "unsubscribed":
                    self.stats["unsubscribed_by_venue"] += 1
                    self._gap_sid(sid, R_UNSUBSCRIBED_BY_VENUE)
                    return "UNSUBSCRIBED"
            return "ERROR" if typ == "error" else "IGNORED"
        if a["ended"]:
            self.stats["ignored_dead_sid"] += 1
            return "ERROR" if typ == "error" else "IGNORED_DEAD_SID"
        if typ == "unsubscribed":
            # we never send `unsubscribe`: the venue ended it
            self.stats["unsubscribed_by_venue"] += 1
            self._end(sid, R_UNSUBSCRIBED_BY_VENUE)
            return "UNSUBSCRIBED"
        if typ == "error" and code in TERMINAL_ERROR_CODES:
            self._end(sid, R_SUB_ERROR)
            return "ERROR"
        seq = m.get("seq")
        out = self._control_seq(a, sid, seq, m.get("id"))
        if out in ("GAP", "AMBIGUOUS"):
            self._gap_acked(a, sid, R_SEQ_GAP if out == "GAP"
                            else R_CONTROL_SEQUENCE_AMBIGUOUS, seq,
                            data=False)
            return "GAP"
        return "ERROR" if typ == "error" else "OK"

    def _acked_data(self, a, sid, typ, t, seq, msg, m, at) -> str:
        if a["ended"]:
            self.stats["ignored_dead_sid"] += 1
            return "IGNORED_DEAD_SID"
        expected = (a["last_data"] if a["mode"] == SEPARATE
                    else (self.sid_seq.get(sid) or 0)) + 1
        if not self._data_seq(a, sid, seq):
            why = R_SEQ_GAP
            if typ == SNAP:
                self.stats["snapshots_out_of_sequence"] += 1
                if seq < expected:
                    why = R_SNAPSHOT_OUT_OF_SEQUENCE
            self._gap_acked(a, sid, why, seq, data=True)
            return "GAP"
        b = self.books.get(t)
        if b is None or t in self.forgotten:
            # a market we no longer track (or never did): its seq is kept
            self.stats["ignored_not_tracked"] += 1
            return "IGNORED_NOT_TRACKED"
        if typ == SNAP:
            self._apply_snapshot(t, b, sid, seq, msg, m, at)
            a["awaiting"].discard(t)
            need = self.recover.get(sid)
            if need is not None:
                need.discard(t)
            return "SNAPSHOT"
        return self._apply_delta(t, b, sid, seq, msg, m, at)

    def _apply_snapshot(self, t, b, sid, seq, msg, m, at) -> None:
        yes = {_d(p): _d(q) for p, q in msg.get("yes_dollars_fp") or []}
        no = {_d(p): _d(q) for p, q in msg.get("no_dollars_fp") or []}
        self._touch(t, b, yes={p: q for p, q in yes.items() if q > 0},
                    no={p: q for p, q in no.items() if q > 0},
                    sid=sid, state=CURRENT, snapshot_at=at,
                    updated_at=at, venue_ts_ms=m.get("sending_ts_ms"),
                    seq=seq, why=None)
        self.sid_markets.setdefault(sid, set()).add(t)
        self.ticker_sid[t] = sid
        self.resubscribe.discard(t)
        self.left_current.discard(t)
        self.stats["snapshots"] += 1

    def _apply_delta(self, t, b, sid, seq, msg, m, at) -> str:
        if b["sid"] != sid or b["state"] != CURRENT:
            self.stats["ignored_after_gap"] += 1
            return "IGNORED_NOT_CURRENT"
        side = str(msg.get("side") or "").lower()
        if side not in ("yes", "no"):
            a = self.anchors.get(sid)
            if a is not None:
                self._gap_acked(a, sid, R_SUB_ERROR, seq, data=True)
            else:
                self._gap_sid(sid, R_SUB_ERROR)
            return "GAP"
        lv = b[side]
        p, dq = _d(msg.get("price_dollars")), _d(msg.get("delta_fp"))
        q = lv.get(p, Decimal(0)) + dq
        if q > 0:
            lv[p] = q
        else:
            lv.pop(p, None)
        self._touch(t, b, updated_at=at, seq=seq,
                    venue_ts_ms=msg.get("ts_ms") or m.get("sending_ts_ms"))
        self.stats["deltas"] += 1
        return "DELTA"

    # ── the subscriber's records on an acknowledged sid ──
    def command_sent(self, sid, cid=None) -> None:
        """An update command (id `cid`) was sent on an acknowledged sid."""
        a = self.anchors.get(sid)
        if a is not None:
            a["n_cmds"] += 1
            if cid is not None:
                a["cmd_index"][cid] = a["n_cmds"]
                while len(a["cmd_index"]) > MAX_CMD_INDEX:
                    a["cmd_index"].pop(next(iter(a["cmd_index"])))

    def requested(self, sid, tickers) -> None:
        """A get_snapshot naming `tickers` was sent on `sid`: outstanding
        until each book is CURRENT again (or dropped)."""
        a = self.anchors.get(sid)
        if a is not None:
            a["awaiting"].update(t for t in tickers if t in self.books)

    def not_held(self, sid, tickers) -> None:
        """The venue's own list of the sid's tickers lacks these: no
        snapshot will come for them on it -- GAP, nothing outstanding."""
        a = self.anchors.get(sid)
        for t in tickers:
            b = self.books.get(t)
            if b is None:
                continue
            self.stats["not_held_by_venue"] += 1
            if a is not None:
                a["awaiting"].discard(t)
            need = self.recover.get(sid)
            if need is not None:
                need.discard(t)
            if not self._current_elsewhere(b, sid):
                self._touch(t, b, state=GAP, why=R_NOT_HELD_BY_VENUE)
            self.resubscribe.add(t)
            ms = self.sid_markets.get(sid)
            if ms is not None:
                ms.discard(t)
            if self.ticker_sid.get(t) == sid:
                self.ticker_sid.pop(t, None)

    def awaiting(self, sid) -> set:
        a = self.anchors.get(sid)
        return set(a["awaiting"]) if a is not None else set()

    def on_message(self, m: dict, *, recv_at: float | None = None) -> str:
        """Apply one decoded WS message; returns what happened."""
        at = float(recv_at if recv_at is not None else self.clock())
        typ = m.get("type")
        sid = m.get("sid")
        if typ == "subscribed":
            # (the subscriber binds the ack first, which anchors the sid
            # with its markets; standalone, an announcement revives a dead
            # number never acknowledged as ours)
            s = (m.get("msg") or {}).get("sid", sid)
            if s is not None and s not in self.anchors and \
                    not self._unknown(s):
                self._seen(s)
                self._revive(s)
            return "SUBSCRIBED"
        if typ in ("ok", "unsubscribed", "error"):
            return self._control(m, typ, sid)
        if typ not in (SNAP, DELTA):
            return "IGNORED"
        msg = m.get("msg") or {}
        t = msg.get("market_ticker")
        seq = m.get("seq")
        if t is None or sid is None or seq is None:
            return "MALFORMED"
        a = self.anchors.get(sid)
        if a is not None:
            return self._acked_data(a, sid, typ, t, seq, msg, m, at)
        if self._unknown(sid):
            self.stats["ignored_unknown_sid"] += 1
            return "IGNORED_UNKNOWN_SID"
        self._seen(sid)
        if t in self.forgotten:
            return self._not_tracked(typ, sid, seq)
        if sid in self.dead_sids:
            # gapped: nothing on it is applied. A snapshot still in flight
            # on it names its market for a fresh snapshot ONLY when the gap
            # could not: the market was not known on the sid and no live
            # sid serves or awaits it. Each market once per dead sid. (No
            # command is ever sent for a sid never acknowledged as ours.)
            if typ != SNAP:
                self.stats["ignored_after_gap"] += 1
                return "IGNORED_NOT_CURRENT"
            self.stats["ignored_dead_sid"] += 1
            b = self._book(t)
            on_dead = self.sid_markets.setdefault(sid, set())
            if t not in on_dead and not self._held_elsewhere(t, b, sid):
                self._touch(t, b, state=GAP, why=R_SEQ_GAP)
                self.resubscribe.add(t)
            on_dead.add(t)
            return "IGNORED_DEAD_SID"
        last = self.sid_seq.get(sid)
        if typ == SNAP:
            if last is None:
                last = self._first_snapshot_after(sid, seq)
            if last is not None and seq != last + 1:
                # messages of this sid were lost before this snapshot (seq
                # past last + 1), or it is a replayed / out-of-order snapshot
                # (seq <= last, red-team scenario 4: applying it rolled a
                # CURRENT book back): never applied, every book of the sid
                # goes GAP, this market's too
                self.stats["snapshots_out_of_sequence"] += 1
                self.sid_markets.setdefault(sid, set()).add(t)
                self._gap_sid(sid, R_SNAPSHOT_OUT_OF_SEQUENCE
                              if seq <= last else R_SEQ_GAP)
                return "GAP"
            b = self._book(t)
            self._apply_snapshot(t, b, sid, seq, msg, m, at)
            self.sid_seq[sid] = seq
            self.sid_pre.pop(sid, None)
            return "SNAPSHOT"
        # delta: the sid's sequence first, then this market's book
        if last is None:
            # before the first snapshot of a sid never acknowledged as ours:
            # never applied, not its sequence, but remembered
            # (_first_snapshot_after)
            self._pre_snapshot(sid, seq)
            self.stats["ignored_after_gap"] += 1
            return "IGNORED_NOT_CURRENT"
        if seq != last + 1:
            self._gap_sid(sid, R_SEQ_GAP)
            return "GAP"
        self.sid_seq[sid] = seq
        b = self._book(t)
        return self._apply_delta(t, b, sid, seq, msg, m, at)

    def current(self, t: str) -> dict:
        b = self.books.get(t)
        if b is None:
            return {"ok": False, "state": UNSUBSCRIBED, "book": None}
        ok = self.connected and b["state"] == CURRENT and \
            b["sid"] is not None and self.sid_seq.get(b["sid"]) is not None
        return {"ok": ok, "state": b["state"] if ok or b["state"] != CURRENT
                else GAP, "why": b["why"],
                "book": self.book_of(t) if ok else None,
                "snapshot_at": b["snapshot_at"],
                "updated_at": b["updated_at"]}

    def book_of(self, t: str) -> dict:
        """The REST orderbook_fp shape (bids ascending), so the one reader
        (kalshi_market_data.book_from_orderbook) derives the asks."""
        b = self.books[t]

        def lv(x):
            return [[str(p), str(q)] for p, q in sorted(x.items())]
        return {"orderbook_fp": {"yes_dollars": lv(b["yes"]),
                                 "no_dollars": lv(b["no"])}}

    def counts(self) -> dict:
        st: dict = {}
        for b in self.books.values():
            st[b["state"]] = st.get(b["state"], 0) + 1
        return {"markets": len(self.books), "by_state": st,
                "current": sum(1 for t in self.books
                               if self.current(t)["ok"]),
                "connected": self.connected,
                "subscriptions": {str(s): {"mode": a["mode"],
                                           "ended": a["ended"],
                                           "awaiting": len(a["awaiting"])}
                                  for s, a in sorted(self.anchors.items(),
                                                     key=lambda x: str(x[0]))},
                **self.stats}


# ── commands ─────────────────────────────────────────────────────────────

#: the update_subscription actions this runtime sends (docs: add_markets /
#: delete_markets / get_snapshot); nothing else ever reaches the socket
UPDATE_ACTIONS = ("add_markets", "delete_markets", "get_snapshot")


class Commands:
    """The two commands the socket carries: ONE `subscribe` per connection,
    then `update_subscription` on its sid. (There is no `unsubscribe`: a
    dead sid is never unsubscribed -- the venue may have reused its number
    for a live subscription, and that unsubscribe would end it.)"""

    def __init__(self):
        self.n = 0

    def _id(self):
        self.n += 1
        return self.n

    def subscribe(self, tickers: list) -> dict:
        return {"id": self._id(), "cmd": "subscribe", "params": {
            "channels": [CHANNEL], "market_tickers": list(tickers)}}

    def update_subscription(self, sid, action: str, tickers: list) -> dict:
        if action not in UPDATE_ACTIONS:
            raise ValueError(action)
        return {"id": self._id(), "cmd": "update_subscription", "params": {
            "sid": sid, "market_tickers": list(tickers), "action": action}}


def chunks(xs: list, n: int | None = None):
    n = SUBSCRIBE_CHUNK if n is None else n
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# ── the runtime (one connection) ─────────────────────────────────────────

class SessionEnd(RuntimeError):
    """The session ends itself, fail closed (every book GAP; the run loop
    reconnects after its backoff); `code` names why (the heartbeat's
    session_end)."""

    code = "KALSHI_WS_SESSION_END"

    def __init__(self, detail: str = ""):
        super().__init__("%s%s" % (self.code, (": " + detail) if detail
                                   else ""))


class ResubscribeStorm(SessionEnd):
    """A market was asked for a snapshot more than
    MAX_RESUBSCRIBES_WITHOUT_RECOVERY times with no snapshot making it
    CURRENT in between: the session's repairs are not converging (RC6
    plane hang; kept as defence in depth)."""

    code = R_RESUBSCRIBE_STORM


class CommandRateBound(SessionEnd):
    """More than MAX_COMMANDS_PER_MINUTE commands in 60 s of one session."""

    code = R_COMMAND_RATE


class SnapshotRequestBound(SessionEnd):
    """One market named by more than MAX_SNAPSHOT_REQUESTS_PER_MARKET
    get_snapshot requests in one session."""

    code = R_SNAPSHOT_REQUEST_BOUND


class GapDuringRecovery(SessionEnd):
    """A second gap on the subscription while its get_snapshot was still
    outstanding."""

    code = R_GAP_DURING_RECOVERY


class SubscriptionEnded(SessionEnd):
    """The venue ended our subscription (error 10 / 25, or
    `unsubscribed`)."""

    code = R_SUBSCRIPTION_ENDED


class SubscribeRefused(SessionEnd):
    """The venue answered our one subscribe with an error (or with `ok`,
    a merge into a subscription this connection never had)."""

    code = R_SUBSCRIBE_REFUSED


_FATAL = {R_GAP_DURING_RECOVERY: GapDuringRecovery,
          R_SUBSCRIPTION_ENDED: SubscriptionEnded,
          R_SUBSCRIBE_REFUSED: SubscribeRefused}
#: the bounds that end a session because its repairs ran away
STORM_CODES = frozenset((R_RESUBSCRIBE_STORM, R_COMMAND_RATE,
                         R_SNAPSHOT_REQUEST_BOUND))
#: diagnostics: connections of this process that logged them so far
_DIAG = {"connections": 0}


class Subscriber:
    """One authenticated connection: ONE subscribe, then update_subscription
    (add_markets / delete_markets / get_snapshot) on its sid; apply
    messages; on a gap ask the sid for fresh snapshots; reconnect on a drop.
    `connect` is an async callable returning a socket with async send /
    recv / close (websockets in production, a fake in tests).

    THE REPAIRS ARE BOUNDED (RC6 plane hang, 2026-10-09). Production
    sportsassets-market-plane, RC6.1 (3d5af039), one connection, no
    disconnect: 4,716 gaps, 1,286,756 resubscribes, 1,440,903 snapshots on
    dead sids, every one of 397 books GAP (pm-acceptance 37954055578,
    venues.json KALSHI_HEALTH.mechanism.ws). The root cause (RC6.2): the
    chunked subscribes 2..n merged into sid 1 and were answered by `ok`,
    which the books ignored without advancing the sid, so the next snapshot
    looked like a gap; markets of those chunks were never bound; each of
    their snapshots on the "dead" sid subscribed again, which merged into
    the live sid, whose `ok` killed it in turn. Now the command set is the
    documented one (module docstring) and every repair is bounded: one
    get_snapshot per gap, a second gap while it is outstanding ends the
    session, at most MAX_COMMANDS_PER_MINUTE commands a minute and
    MAX_SNAPSHOT_REQUESTS_PER_MARKET requests per market a session, and the
    plane-hang bound (MAX_RESUBSCRIBES_WITHOUT_RECOVERY) kept beneath them;
    past any of them the session ends (SessionEnd, named), every book GAP,
    and the run loop reconnects after its backoff.

    `gap_sink` (the worker): an async callable given the markets whose book
    just left CURRENT; it is awaited (bounded by gap_sink_timeout_s) before
    the next frame is read, so their rows are unreadable before anything
    after the gap is applied."""

    def __init__(self, connect, books: WsBooks, *, wanted,
                 clock=time.time, backoff=(1, 2, 5, 10, 30, 60),
                 idle_recheck_s: float | None = None, gap_sink=None,
                 gap_sink_timeout_s: float = 2.0):
        self.connect = connect
        self.books = books
        self.wanted = wanted            # callable -> list of tickers
        self.clock = clock
        self.backoff = backoff
        self.idle_recheck_s = (IDLE_RECHECK_S if idle_recheck_s is None
                               else idle_recheck_s)
        self.gap_sink = gap_sink
        self.gap_sink_timeout_s = gap_sink_timeout_s
        self.cmd = Commands()
        # the first command id of the current session
        self.first_id = 1
        #: markets this session subscribed or has queued to add
        self.subscribed: set = set()
        #: command id -> the markets it named, until the venue answers it
        self.pending: dict = {}
        self.connections = 0
        #: gap recoveries (one get_snapshot request per gap, in chunks)
        self.resubscribes = 0
        self.last_error = None
        # market -> snapshot requests since a snapshot last made it CURRENT
        # (this session); sessions ended by a storm bound, counted
        self.unrecovered: dict = {}
        self.storms = 0
        #: (RC6.2) session ends by code (process lifetime) and the last one
        self.session_ends: dict = {}
        self.last_end_reason = None
        #: (RC6.2) commands sent by kind (process lifetime)
        self.commands_sent: dict = {}
        self.gap_sink_failures = 0
        self.frames = 0
        #: bumped by wanted_changed(); see _fresh_wanted
        self.wanted_version = 0
        self._reset_session()
        self._diag = None

    # ── per session ──
    def _reset_session(self) -> None:
        self.subscribed = set()
        self.pending = {}
        self.sid = None                 # our subscription's sid, once acked
        self.sub_id = None              # our one subscribe's command id
        self.queued_add: list = []      # to add_markets once the sid is known
        self.queued_delete: set = set()
        self.rewant: list = []          # dropped, wanted again, still held
        self.readd: list = []           # the venue's list lacked them
        self.on_venue: dict = {}        # ticker -> id of the command adding it
        self.cmd_kind: dict = {}        # command id -> its kind
        self.window = collections.deque()
        self.snap_requests: dict = {}
        self.readds: dict = {}
        self.unrecovered = {}
        self._w_obj, self._w_len, self._w_ver, self._w_frames = \
            None, -1, -1, 0

    def wanted_changed(self) -> None:
        """The caller replaced the CONTENTS of the list `wanted` returns
        (the worker's refresh): the next step re-reads it in full."""
        self.wanted_version += 1

    def _fresh_wanted(self) -> list:
        """The wanted markets not yet subscribed this session. Re-reading
        the wanted set is O(markets); it runs after every frame, so when
        `wanted` returns the SAME list object as last time, with the same
        length and no wanted_changed() since, it is re-read only every
        WANTED_RECHECK_FRAMES frames (a 50,000-frame burst on 2,000 markets
        spent most of its time here). A callable returning a new list each
        time is read in full every time."""
        w = self.wanted()
        self._w_frames += 1
        if w is self._w_obj and len(w) == self._w_len and \
                self._w_ver == self.wanted_version and \
                self._w_frames < WANTED_RECHECK_FRAMES:
            return []
        self._w_obj, self._w_len = w, len(w)
        self._w_ver, self._w_frames = self.wanted_version, 0
        return [t for t in w if t not in self.subscribed]

    def forget(self, tickers) -> int:
        """(RC6) The markets no longer wanted: their books dropped
        (WsBooks.forget), out of the tracked subscription and of every
        command not yet answered; (RC6.2) the venue's subscription loses
        them by delete_markets, sent by the session on its sid. Returns how
        many books were dropped."""
        gone = set(tickers)
        held = sum(1 for t in gone if t in self.books.books)
        self.books.forget(gone)
        self.subscribed.difference_update(gone)
        for t in gone:
            self.unrecovered.pop(t, None)
            if t in self.on_venue:
                self.queued_delete.add(t)
        # an unanswered command no longer names them
        for k, ts in list(self.pending.items()):
            self.pending[k] = [t for t in ts if t not in gone]
        self.queued_add = [t for t in self.queued_add if t not in gone]
        self.rewant = [t for t in self.rewant if t not in gone]
        self.readd = [t for t in self.readd if t not in gone]
        return held

    def _end(self, cls, detail: str = ""):
        code = cls.code
        self.session_ends[code] = self.session_ends.get(code, 0) + 1
        self.last_end_reason = code
        if code in STORM_CODES:
            self.storms += 1
        raise cls(detail)

    def _check_fatal(self) -> None:
        f = self.books.fatal
        if f is not None:
            self.books.fatal = None
            self._end(_FATAL.get(f, SessionEnd), f)

    # ── sending ──
    async def _send(self, ws, cmd, sid=None) -> None:
        """Every record of a command is made BEFORE its send: `send` can
        suspend (write backpressure), and the worker's prune runs then."""
        now = self.clock()
        while self.window and now - self.window[0] >= 60.0:
            self.window.popleft()
        if len(self.window) >= MAX_COMMANDS_PER_MINUTE:
            self._end(CommandRateBound, "%d commands in 60 s"
                      % len(self.window))
        self.window.append(now)
        kind = cmd["cmd"] if cmd["cmd"] == "subscribe" else \
            cmd["params"]["action"]
        self.cmd_kind[cmd["id"]] = kind
        self.commands_sent[kind] = self.commands_sent.get(kind, 0) + 1
        if sid is not None:
            self.books.command_sent(sid, cmd["id"])
        while len(self.pending) > MAX_PENDING_SUBSCRIBES:
            self.pending.pop(next(iter(self.pending)))
        self._diag_cmd(cmd)
        await ws.send(json.dumps(cmd))

    def _count_requests(self, tickers) -> None:
        for t in tickers:
            if self.snap_requests.get(t, 0) >= \
                    MAX_SNAPSHOT_REQUESTS_PER_MARKET:
                self._end(SnapshotRequestBound, "%d markets, e.g. %s"
                          % (len(tickers), t))
            if self.unrecovered.get(t, 0) >= \
                    MAX_RESUBSCRIBES_WITHOUT_RECOVERY:
                self._end(ResubscribeStorm, (
                    "%d market(s) asked for a snapshot %d times without one "
                    "making them current (e.g. %s)")
                    % (len(tickers), MAX_RESUBSCRIBES_WITHOUT_RECOVERY, t))
        for t in tickers:
            self.snap_requests[t] = self.snap_requests.get(t, 0) + 1
            self.unrecovered[t] = self.unrecovered.get(t, 0) + 1

    async def _commands(self, ws) -> None:
        """What the session owes the venue now, in one burst: the one
        subscribe (once anything is wanted); then, on the acknowledged sid,
        delete_markets of dropped markets, add_markets of new ones (and the
        one re-add of a market the venue's list lacked), get_snapshot of
        the markets a gap named (and of a dropped market wanted again that
        the venue still holds). Yields to the event loop after a burst.
        Cheap when there is nothing to do (it runs after every frame)."""
        sent = 0
        fresh = self._fresh_wanted()
        if fresh:
            self.books.want(fresh)
            self.subscribed.update(fresh)
            for t in fresh:
                if t in self.queued_delete:
                    # dropped and wanted again before its delete went out:
                    # the venue still holds it; only its snapshot is needed
                    self.queued_delete.discard(t)
                    self.rewant.append(t)
                else:
                    self.queued_add.append(t)
        if self.sub_id is None:
            first = sorted(t for t in self.queued_add
                           if t not in self.books.forgotten)
            if first:
                c = next(iter(chunks(first)))
                self.queued_add = first[len(c):]
                cmd = self.cmd.subscribe(c)
                self.sub_id = cmd["id"]
                self.pending[cmd["id"]] = list(c)
                for t in c:
                    self.on_venue[t] = cmd["id"]
                await self._send(ws, cmd)
                sent += 1
        sid = self.sid
        if sid is not None:
            if self.queued_delete:
                ts = sorted(self.queued_delete)
                self.queued_delete = set()
                for c in chunks(ts):
                    cmd = self.cmd.update_subscription(sid, "delete_markets",
                                                       c)
                    self.pending[cmd["id"]] = []
                    for t in c:
                        self.on_venue.pop(t, None)
                    await self._send(ws, cmd, sid)
                    sent += 1
            adds = sorted(set(self.queued_add) | set(self.readd)) if (
                self.queued_add or self.readd) else ()
            self.queued_add, self.readd = [], []
            for c in chunks(adds):
                # a drop during an earlier send in this burst
                c = [t for t in c if t not in self.books.forgotten
                     and t in self.books.books]
                if not c:
                    continue
                cmd = self.cmd.update_subscription(sid, "add_markets", c)
                self.pending[cmd["id"]] = list(c)
                for t in c:
                    self.on_venue[t] = cmd["id"]
                # bound to the sid when SENT (the sid is known)
                self.books.bind(sid, c)
                await self._send(ws, cmd, sid)
                sent += 1
            gap = self.books.recover.pop(sid, None) or set()
            need = ()
            if gap or self.rewant:
                want_snap = set(gap) | set(self.rewant)
                self.rewant = []
                outstanding = self.books.awaiting(sid)
                need = sorted(t for t in want_snap if t in self.books.books
                              and t not in self.books.forgotten
                              and t not in outstanding)
            if need:
                if gap:
                    self.resubscribes += 1
                for c in chunks(need):
                    c = [t for t in c if t not in self.books.forgotten
                         and t in self.books.books]
                    if not c:
                        continue
                    self._count_requests(c)
                    cmd = self.cmd.update_subscription(sid, "get_snapshot", c)
                    self.pending[cmd["id"]] = list(c)
                    self.books.bind(sid, c)
                    self.books.requested(sid, c)
                    await self._send(ws, cmd, sid)
                    sent += 1
        if sent:
            await asyncio.sleep(0)

    # ── receiving ──
    def _on_frame(self, m: dict) -> str:
        typ = m.get("type")
        self._diag_frame(m, typ)
        cid = m.get("id")
        if typ == "subscribed":
            sid = (m.get("msg") or {}).get("sid")
            if self.sid is None and self.sub_id is not None and \
                    cid == self.sub_id and sid is not None:
                self.sid = sid
                self.books.bind(sid, self.pending.pop(cid, None) or [],
                                ours=True)
            else:
                self.books.bind(sid, [], ours=False)
            return self.books.on_message(m, recv_at=self.clock())
        if self.sid is None and self.sub_id is not None and \
                cid == self.sub_id and typ in ("ok", "error"):
            # our one subscribe answered by anything but `subscribed`
            self.books.fatal = self.books.fatal or R_SUBSCRIBE_REFUSED
        out = self.books.on_message(m, recv_at=self.clock())
        if typ == "ok":
            self._reconcile(m)
        elif typ == "error" and type(cid) is int:
            self.pending.pop(cid, None)
        elif out == "SNAPSHOT":
            # its book is CURRENT again: its repairs converged
            self.unrecovered.pop((m.get("msg") or {}).get("market_ticker"),
                                 None)
        return out

    def _reconcile(self, m: dict) -> None:
        """An `ok` on our sid answering one of our update commands carries
        the venue's full ticker list after it: a market a command up to it
        added and the list lacks is GAP and added again ONCE (a second
        absence leaves it GAP); a listed market we no longer want has no
        book, so it is never CURRENT."""
        cid = m.get("id")
        if type(cid) is not int:
            return
        self.pending.pop(cid, None)
        # only an add / delete changes the subscription, and its `ok` is
        # documented to carry the full list after it (a get_snapshot's list
        # is not relied on)
        if self.cmd_kind.get(cid) not in ("add_markets", "delete_markets") \
                or self.sid is None or m.get("sid") != self.sid:
            return
        listed = (m.get("msg") or {}).get("market_tickers")
        if not isinstance(listed, list):
            return
        holds = set(listed)
        missing = sorted(t for t, at in self.on_venue.items()
                         if at <= cid and t not in holds
                         and t in self.books.books)
        if not missing:
            return
        self.books.not_held(self.sid, missing)
        for t in missing:
            self.on_venue.pop(t, None)
            if self.readds.get(t, 0) < 1:
                self.readds[t] = self.readds.get(t, 0) + 1
                self._count_requests([t])
                self.readd.append(t)

    async def _drain_gaps(self) -> None:
        if not self.books.left_current:
            return
        ts = sorted(self.books.left_current)
        self.books.left_current.clear()
        if self.gap_sink is None:
            return
        try:
            await asyncio.wait_for(self.gap_sink(ts), self.gap_sink_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception:                                       # noqa: BLE001
            self.gap_sink_failures += 1

    async def session(self, *, max_messages: int | None = None) -> None:
        ws = await self.connect()
        self.connections += 1
        self.books.on_connected()
        self._diag_start()
        try:
            self._reset_session()
            # A NEW CONNECTION HAS NO SUBSCRIPTION: the one subscribe covers
            # every wanted market (its first chunk; add_markets the rest).
            # The previous connection's disconnect left every book in the
            # resubscribe set; each book stays GAP until its fresh snapshot.
            self.books.want(list(self.wanted()))
            self.first_id = self.cmd.n + 1
            self.books.resubscribe.clear()
            await self._commands(ws)
            n = 0
            burst = 0
            while max_messages is None or n < max_messages:
                # NOTHING TO READ IS NO REASON TO STOP LOOKING AT THE WANTED
                # SET: a session that started with it empty (the worker's
                # first read not landed yet) subscribed nothing, so the
                # venue sent nothing (review of ca102147). recv() is
                # cancellation-safe (websockets): no message is lost.
                try:
                    raw = await asyncio.wait_for(ws.recv(),
                                                 self.idle_recheck_s)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    n += 1
                    burst += 1
                    self.frames += 1
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        m = None
                    if isinstance(m, dict):
                        self._on_frame(m)
                        await self._drain_gaps()
                        self._check_fatal()
                    if burst >= YIELD_EVERY_FRAMES:
                        # a burst of frames never holds the shared loop
                        burst = 0
                        await asyncio.sleep(0)
                await self._commands(ws)
        finally:
            self.books.on_disconnected()
            try:
                await self._drain_gaps()
            except Exception:                                   # noqa: BLE001
                pass
            try:
                await ws.close()
            except Exception:                                   # noqa: BLE001
                pass

    async def run(self, *, stop=None) -> None:
        i = 0
        while stop is None or not stop.is_set():
            try:
                await self.session()
                i = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:                            # noqa: BLE001
                self.last_error = type(exc).__name__
            await asyncio.sleep(self.backoff[min(i, len(self.backoff) - 1)])
            i += 1

    # ── bounded diagnostics (the unknowns, from production) ──
    def _diag_start(self) -> None:
        if _DIAG["connections"] < DIAG_CONNECTIONS:
            _DIAG["connections"] += 1
            self._diag = {"c": _DIAG["connections"], "n": 0, "ctrl": 0,
                          "cmd": 0, "next": set(), "data_seq": {}}
        else:
            self._diag = None

    def _diag_frame(self, m: dict, typ) -> None:
        d = self._diag
        if d is None:
            return
        d["n"] += 1
        sid = m.get("sid")
        if typ in (SNAP, DELTA):
            if sid in d["next"]:
                d["next"].discard(sid)
                log.info("KWS_NEXT c=%d sid=%s type=%s seq=%s", d["c"], sid,
                         typ, m.get("seq"))
            d["data_seq"][sid] = m.get("seq")
            return
        if d["ctrl"] >= DIAG_MAX_CONTROL:
            return
        d["ctrl"] += 1
        if typ == "subscribed":
            sid = (m.get("msg") or {}).get("sid", sid)
        body = m.get("msg") if isinstance(m.get("msg"), dict) else {}
        listed = body.get("market_tickers")
        log.info("KWS_CTRL c=%d n=%d type=%s id=%s sid=%s seq=%s "
                 "prev_data_seq=%s code=%s n_tickers=%s", d["c"], d["n"], typ,
                 m.get("id"), sid, m.get("seq"), d["data_seq"].get(sid),
                 body.get("code"), len(listed) if isinstance(listed, list)
                 else "-")
        if sid is not None:
            d["next"].add(sid)

    def _diag_cmd(self, cmd: dict) -> None:
        d = self._diag
        if d is None or d["cmd"] >= DIAG_MAX_COMMANDS:
            return
        d["cmd"] += 1
        p = cmd.get("params") or {}
        sids = p.get("sids") or ([p["sid"]] if "sid" in p else [])
        log.info("KWS_CMD c=%d id=%s cmd=%s action=%s sids=%s n_tickers=%d",
                 d["c"], cmd.get("id"), cmd.get("cmd"), p.get("action"),
                 ",".join(str(s) for s in sids) or "-",
                 len(p.get("market_tickers") or ()))


def websockets_connect(key_id: str, private_key, *, url: str = WS_URL):
    """The production connector (websockets, keepalive pings)."""
    async def connect():
        import websockets
        headers = auth_headers(key_id, private_key, "GET", WS_PATH)
        return await websockets.connect(url, additional_headers=headers,
                                        ping_interval=10, ping_timeout=10,
                                        max_size=2 ** 22)
    return connect


# ── which mechanism serves Kalshi books (closeout 2026-10-08) ───────────

KALSHI_WS = "KALSHI_WS"
KALSHI_REST_FALLBACK = "KALSHI_REST_FALLBACK"
BEAT_MAX_AGE_S = 120.0


def mechanism(rest_detail: dict | None, ws_beat: dict | None,
              plane_beat: dict | None, *, now: float) -> dict:
    """PURE. Kalshi's market-data mechanism, from the three records that
    exist (never assumed): the REST worker's freshness (current books by
    persisted basis over the tracked denominator), the WebSocket runtime's
    heartbeat and the dedicated plane's boot record. KALSHI_WS is primary
    only when the plane is up and unrefused, the WebSocket runtime beats
    connected with >= 1 current book, and >= 1 tracked book is current on
    the WebSocket basis; otherwise KALSHI_REST_FALLBACK with every reason
    named. Beats are {"status", "detail", "at"}."""
    f = (rest_detail or {}).get("freshness") or {}
    by = f.get("current_by_source") or {}
    ws_cur, rest_cur = int(by.get("WS") or 0), int(by.get("REST") or 0)
    den = f.get("denominator")
    why = []
    pd = (plane_beat or {}).get("detail") or {}
    if plane_beat is None:
        why.append("MARKET_PLANE_ABSENT")
    elif now - float(plane_beat.get("at") or 0.0) > BEAT_MAX_AGE_S:
        why.append("MARKET_PLANE_HEARTBEAT_STALE")
    elif (pd.get("guard") or {}).get("refused"):
        why.append("MARKET_PLANE_REFUSED:%s" % pd["guard"]["refused"])
    wd = (ws_beat or {}).get("detail") or {}
    ws = wd.get("ws") or {}
    if ws_beat is None:
        why.append("KALSHI_WS_HEARTBEAT_ABSENT")
    elif now - float(ws_beat.get("at") or 0.0) > BEAT_MAX_AGE_S:
        why.append("KALSHI_WS_HEARTBEAT_STALE")
    elif wd.get("state") == "OWNER_ACTION_REQUIRED":
        why.append("KALSHI_WS_%s" % (wd.get("why") or "OWNER_ACTION"))
    else:
        if ws.get("connected") is not True:
            why.append("KALSHI_WS_NOT_CONNECTED")
        if int(ws.get("current") or 0) <= 0:
            why.append("KALSHI_WS_NO_CURRENT_BOOK")
    if ws_cur <= 0:
        why.append("NO_TRACKED_BOOK_CURRENT_ON_WS_BASIS")
    wsf = wd.get("freshness") or {}
    return {
        "mechanism": KALSHI_REST_FALLBACK if why else KALSHI_WS,
        "why": why,
        "plane": {"present": plane_beat is not None,
                  "commit": pd.get("commit"), "mode": (pd.get("guard") or {})
                  .get("mode"), "process_locked": (pd.get("guard") or {})
                  .get("process_locked"),
                  "age_s": (None if plane_beat is None else
                            round(now - float(plane_beat.get("at") or 0), 1))},
        "ws": {"state": wd.get("state") or (ws_beat or {}).get("status"),
               "connected": ws.get("connected"),
               "current_books": ws.get("current"),
               "subscribed_markets": wd.get("subscribed_markets"),
               "resubscribes": wd.get("resubscribes"),
               "connections": wd.get("connections"),
               # (RC6) what caused the resubscribes, as the books counted
               # it: a resubscribe follows a gap, a sid error or a snapshot
               # on a dead sid -- never a reconnect (that is `connections`)
               "gaps": ws.get("gaps"), "errors": ws.get("errors"),
               "disconnects": ws.get("disconnects"),
               "ignored_dead_sid": ws.get("ignored_dead_sid"),
               "by_state": ws.get("by_state"),
               "current_book_update_age_s": wd.get(
                   "current_book_update_age_s"),
               "numerator": wsf.get("numerator"),
               "denominator": wsf.get("denominator"),
               "account_limits_status": (wd.get("account_limits") or {})
               .get("status")},
        "tracked": {"denominator": den, "current_ws": ws_cur,
                    "current_rest": rest_cur,
                    "numerator": f.get("numerator"), "rate": f.get("rate"),
                    "ws_share": (round(ws_cur / den, 4) if den else None),
                    "sla_s": f.get("sla_s")},
        "accounting_rule": ("Kalshi freshness = current tracked books from "
                            "ANY basis / tracked books; WS share = those on "
                            "the snapshot-then-sequence WebSocket basis; "
                            "REST counted apart, never merged into WS")}
