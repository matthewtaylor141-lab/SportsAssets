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
    add / delete carries the venue's full ticker list after that command:
    a ticker we want there but the venue lacks (or refused by an error) is
    GAP (R_NOT_HELD_BY_VENUE) and added again ONCE (after error 27, the
    command rate, retried later: RATE_LIMIT_RETRY_S, bounded).
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
    session ends and the reconnect subscribes afresh -- also a 10 / 25
    WITHOUT a sid (the connection holds one subscription; fail closed).
    Every other error is counted; one carrying (sid, seq) is a control
    frame of the sequence. An
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
else is a gap. The guard for the other reading is a BOUND: the highest seq
a separate control counter could carry on a given control frame,
1 + j + CONTROL_SEQ_SLACK for the reply to our j-th update command on the
sid (an unknown or missing id: j = every update command sent on it) --
the subscription's own `subscribed` may have taken number 1 (it carries no
seq, so whether it counted cannot be seen), each of our j commands at most
one more, and at most CONTROL_SEQ_SLACK frames the venue sends unasked
(RC6.2 review, round 3: the bound used to be j + CONTROL_SEQ_SLACK, one
short when both the `subscribed` and an unasked frame took a number -- a
lost delta then hid behind the unasked frame). Then:
  * seq == last + 1: above the bound, or with no book of the sid CURRENT
    (nothing can be hidden in the slot), it advances; otherwise it is a gap
    (R_CONTROL_SEQUENCE_AMBIGUOUS).
  * seq <= last: a shared counter gives the FIRST reply to our command a
    seq above every frame read before it (one ordered connection) -- unless
    a frame was lost after the command went out (the reply may then be a
    late copy, and a gap followed). So the first reply to an update command
    of ours, with no gap on the sid since it was sent, at or below the
    bound, proves a separate counter: the sid switches to `SEPARATE`
    (control frames counted, never sequenced; data frames run by
    themselves). Anything else at or below the last seq -- a second reply,
    a frame answering no command we know, one after a gap, one above the
    bound -- is a REPLAY of the shared sequence: a gap while a book of the
    sid is CURRENT (R_SEQ_GAP), counted otherwise; never a switch (RC6.2
    review, round 3: a replayed `ok` used to switch a shared sid to
    SEPARATE, after which a loss right before the next control frame went
    unseen until the next data frame).
  * seq > last + 1: a gap.
  * In SEPARATE mode a control frame ABOVE the bound proves the shared
    counter after all: a gap, and the sid is SHARED again from its seq; one
    past the last DATA seq + 1 with a book CURRENT -- what a shared counter
    shows after a lost frame (a separate one only on a sid that carried
    fewer data than control frames) -- is a gap (R_CONTROL_SEQUENCE_
    AMBIGUOUS). So even a sid wrongly taken as SEPARATE never keeps a book
    CURRENT across a frame that reveals a loss.
  * A data frame at exactly the last DATA seq + 1 while control frames took
    (or skipped) the slots after it proves a separate counter (a shared
    counter never gives a data frame the seq of a control frame, and a frame
    lost before a later one never arrives after it): SEPARATE, no gap -- also
    right after an ambiguous or skipping control frame's gap, whose books
    are GAP already (RC6.2 review, round 3: that data frame used to be a
    second gap while the get_snapshot was outstanding, ending the session).
Why it is safe either way. Shared counter: a control frame is the next
slot, or (after a loss) above it -- a gap, as for data; a replay is never
taken as anything else. Separate counter: a control frame can take a slot
only while no book of the sid is CURRENT (nothing to hide; the data frames
it may stand in for precede every snapshot applied after it, and a snapshot
is a whole book) or when its seq is beyond any separate counter -- even
when control frames themselves are lost. The bound rests on assumptions
about a SEPARATE counter (the documented shared one needs none of them),
named here and in the proof: C1 the venue answers each of our commands
with at most one control frame, sends at most CONTROL_SEQ_SLACK unasked on
a subscription, and its `subscribed` takes at most one number; C2 it
answers our commands in the order it received them. (Without C2 the only
safe bound is every command sent so far; that bound makes a false gap of
the documented venue's ordinary first `ok`s whenever several add_markets
are in flight on a quiet sid, so it is not used.) And for EITHER reading,
C3: a control frame's seq is a NEW number on its counter, as every data
frame's is -- never a copy of the sid's current seq (an "echo"). Under an
echo counter, data frame k lost and the next `ok` echoing k is exactly what
the documented shared counter sends with nothing lost (data k - 1, then the
`ok` at k), so no client rule covers it short of treating every reply as a
gap; an echo is not the "sequential number" the docs give `ok` /
`unsubscribed` / scoped errors, and RC6.1's storm needed the shared
counter (an echo venue gives rc6/int-62 no gap). The KWS_CTRL diagnostics
(seq against prev_data_seq) settle C3 and the counter's reading in
production. On a sid never acknowledged as ours the bound is 1 + every
command of the connection + CONTROL_SEQ_SLACK.
What it costs: under a shared counter, a false gap only when a sid has
carried no more frames than its update commands + 2 (a one- or two-market
quiet subscription: one gap, one get_snapshot, then the counts part; a
second ambiguous reply while that get_snapshot is outstanding ends the
session -- bounded, fail closed, and the reconnect subscribes every market
in its one subscribe); under a separate counter, none in the usual shape
(the first reply to our command after any data is at or below the last
seq: SEPARATE, no gap), at most one gap and one get_snapshot otherwise (an
unasked frame, a reply after a gap, a sid with more control than data
frames); never a storm.

VENUE MEMBERSHIP (RC6.2 review). Sequence continuity of the sid is not
enough: get_snapshot "returns an orderbook_snapshot for the requested
market_tickers without modifying the subscription" (asyncapi; changelog
2026-04-20: "without adding them to the subscription or affecting the
existing delta stream") -- a snapshot of a market the venue does not hold
is in the sid's sequence, but no delta of that market follows it. And a
snapshot the venue sent before our delete_markets may arrive after the
market is wanted again (add_markets sent), while the venue streams nothing
for it between the two. So a snapshot makes a book CURRENT only when the
venue HOLDS the market on the sid by its own replies, with nothing naming
the market unanswered: held from our subscribe's `subscribed` (every market
it named -- assumption H0: `subscribed` means the venue took them all),
then by every add / delete reply -- the `ok`'s msg.market_tickers is the
full list after that command and settles every command up to it (an `ok`
with no list settles its own command: assumption H1, an add answered `ok`
took its markets). AN ERROR SAYS NOTHING ABOUT MEMBERSHIP (round 4; see
below). A snapshot of a
market not held is in the sequence and never applied (snapshots_not_held);
get_snapshot names only held markets; a held book the list lacks is GAP
(R_NOT_HELD_BY_VENUE); a market whose add or re-add a reply confirms gets
its snapshot -- the add's own when it comes after that reply, otherwise one
get_snapshot. A gap with an add unanswered re-adds the market (the lost
frame may be the reply), and so does an add unanswered REPLY_TIMEOUT_S
after it was sent (a sid that carries nothing else never shows the loss);
with those re-adds spent (MAX_READDS_PER_MARKET) the session ends
(ReplyTimeout, R_REPLY_TIMEOUT: fail closed, the reconnect subscribes
afresh). A market dropped and wanted again before its delete went out is
asked for by get_snapshot only while the venue holds it; otherwise it is
added again. A market the venue may hold although no reply said so (an add
of it whose reply was taken as lost) that a refusal then names is re-added
ALONE: error 26 refuses a whole command for one market, and an add of a held
market alone is "no action", answered by `ok` with the full list (it used to
stay GAP, not held, while the venue streamed it). A market dropped and wanted
again while its add was in flight is asked for by get_snapshot when the
reply confirms it, never left waiting on the add's own snapshot (it is not
bound to the sid, so a gap could not name it if that snapshot were lost).
Bounded: the re-add budgets, the command rate and the per-market
get_snapshot bounds.

AN ERROR SAYS NOTHING ABOUT MEMBERSHIP (round 4, replacing an assumption
rounds 2 and 3 made without naming it: E0, "any error means the command was
not run, the venue is as it was"). No error code is documented to mean that.
Error 18 is "Server timed out while processing command" -- the command may
well have run; error 27 "exceeded its command rate limit" does not say
nothing happened either. Round 3 took a delete answered by an error as
having left the market held, and a market wanted again after it was asked for
by get_snapshot (answered for ANY ticker, "without modifying the
subscription"): when the delete HAD run, the venue's snapshot of a market it
no longer held made the book CURRENT and no delta ever followed -- a stale
book served as current (reproduced against fde50aff:
tests/test_rc62_kalshi_ws_errored_commands.py). So an error answering an add
or a delete leaves its markets UNSURE: a delete leaves the market not held
(no snapshot of it applied, no get_snapshot naming it), an add leaves one not
known held not held; only a reply with the full ticker list says (WsBooks.
_settle, _unknown_effect). A market wanted again meanwhile is added again
ALONE: if the venue holds it, the add is "no action" and its `ok` lists it;
if not, the add takes it and sends its own snapshot -- either way the `ok`'s
list holds it and its confirmation asks for a snapshot (no own snapshot is
waited for from an unsure market). A dropped market the venue may still hold
is deleted again RATE_LIMIT_RETRY_S later (MAX_RATE_LIMIT_RETRIES a
session); an add refused by 18 or 27 is retried later after its one re-add
(RETRY_LATER_ERROR_CODES). Errors still count, end a subscription only when
they are 10 / 25, and carry (sid, seq) into the sequence rule as before.
The assumptions the membership rule rests on are H0 and H1 above and no
other: not E0.

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
#: (RC6.2) control frames a venue with a SEPARATE control counter sends
#: unasked on a subscription, at most (assumption C1): with the one number
#: its `subscribed` may take, the reply to our j-th update command on a sid
#: carries at most 1 + j + this (WsBooks._ctrl_bound); a control frame
#: above that can only be a shared slot
CONTROL_SEQ_SLACK = 1
#: (RC6.2) command ids remembered per sid for that bound (and unanswered
#: add / delete commands), at most
MAX_CMD_INDEX = 1024
#: (RC6.2) errors that END a subscription ("the user must resubscribe"):
#: 10 channel error, 25 subscription buffer overflow
TERMINAL_ERROR_CODES = frozenset((10, 25))
#: (RC6.2 review) the per-subscription command rate limit error
RATE_LIMIT_ERROR = 27
#: (round 4) "Command timeout - Server timed out while processing command":
#: the command may well have run (an error says nothing about it), and the
#: failure is as transient as the rate limit's
TIMEOUT_ERROR = 18
#: the errors an add refused by is retried LATER for (RATE_LIMIT_RETRY_S,
#: MAX_RATE_LIMIT_RETRIES a session) once its one immediate re-add is spent
RETRY_LATER_ERROR_CODES = frozenset((RATE_LIMIT_ERROR, TIMEOUT_ERROR))
#: (RC6.2 review) re-adds of one market in one session after a gap that may
#: have lost the reply to its add, at most (a refusal -- an error, or an
#: `ok` whose list lacks it -- re-adds it once more, its own budget)
MAX_READDS_PER_MARKET = 2
#: (RC6.2 review) a market whose re-add was refused again by error 27 (the
#: command rate; round 4: or 18, a timeout) is added again this long after,
#: at most MAX_RATE_LIMIT_RETRIES times a session (a retry inside the same
#: rate window would be refused again)
RATE_LIMIT_RETRY_S = 30.0
MAX_RATE_LIMIT_RETRIES = 3
#: (RC6.2 review) an add / delete the venue has not answered this long after
#: it was sent: its reply is taken as lost, as at a gap (a market the venue
#: then holds nothing for sends no frame that could reveal the loss)
REPLY_TIMEOUT_S = 30.0
#: (RC6.2 review) a session that lasted this long before it ended resets
#: the reconnect backoff (a session only ever ends by raising, so the index
#: never went back to the first step)
BACKOFF_RESET_AFTER_S = 300.0
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
#: (RC6.2 review) session end: a market's add went unanswered past
#: REPLY_TIMEOUT_S after its re-adds were spent
R_REPLY_TIMEOUT = "KALSHI_WS_REPLY_TIMEOUT"


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
                      "not_held_by_venue": 0,
                      # (RC6.2 review) snapshots of a market the venue was
                      # not known to hold on the sid: in its sequence,
                      # never applied; markets re-added because their add
                      # was unanswered at a gap
                      "snapshots_not_held": 0, "readd_after_gap": 0,
                      # (RC6.2 review, round 3) control frames at or below
                      # the last seq that are no proof of a separate counter
                      # (a second reply, no command we know, a reply after a
                      # gap, above the bound): replays; SEPARATE sids proved
                      # shared after all by a control frame above the bound
                      "control_replayed": 0, "control_seq_shared_again": 0,
                      # (round 4) errors answering our add / delete: the
                      # venue's membership after them is unknown (UNSURE)
                      "membership_unknown_after_error": 0}
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
        #: (RC6.2 review) sid -> {market: error code or None}: markets the
        #: venue refused or did not list (the subscriber decides on a
        #: re-add and clears it)
        self.refused: dict = {}
        #: (RC6.2 review) sid -> markets whose add was unanswered at a gap
        #: (the reply may be the lost frame; the subscriber adds them again)
        self.readd_lost: dict = {}
        #: (RC6) markets the runtime stopped tracking in THIS session (see
        #: `forget`); cleared with the session
        self.forgotten: set = set()
        #: (RC6.2) sid -> the state of a subscription acknowledged as ours:
        #: mode (SHARED / SEPARATE control-frame sequence), last_data (the
        #: highest DATA seq seen), n_cmds (update commands sent on it),
        #: cmd_index (command id -> its 1-based index among them), answered
        #: (round 3: ids of those whose reply arrived -- a second reply is a
        #: replay), ended
        #: (None, or why it ended), awaiting (markets whose requested
        #: snapshot is outstanding); (RC6.2 review, VENUE MEMBERSHIP) known
        #: (market -> (command id, held): what the venue's latest reply that
        #: spoke about it says -- our `subscribed` is command 0), pend
        #: (market -> [(command id, action)] of the add / delete commands
        #: naming it the venue has not answered), cmds (unanswered add /
        #: delete id -> (action, tickers)), expect (market -> the add whose
        #: own snapshot is still to come), unheld_snap (markets a snapshot of
        #: which arrived while an add / delete naming them was unanswered),
        #: unsure (markets whose add's reply may have been lost: whether the
        #: venue holds them is unknown until a reply says)
        self.anchors: dict = {}
        #: (RC6.2 review) commands sent on this connection (every kind): a
        #: separate control counter on any sid cannot pass it (+ the slack)
        self.conn_cmds = 0
        #: (RC6.2 review) sids never acknowledged as ours whose control
        #: frames proved a separate counter (counted, never sequenced)
        self.legacy_separate: set = set()
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
            for ms in self.refused.values():
                ms.pop(t, None)
            for ms in self.readd_lost.values():
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
        self.conn_cmds = 0

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
        self.refused.clear()
        self.readd_lost.clear()
        self.legacy_separate.clear()
        self.conn_cmds = 0
        self.fatal = None
        # a new session subscribes only what is wanted
        self.forgotten.clear()

    def bind(self, sid, tickers, *, ours: bool = True) -> None:
        """The venue acknowledged a subscribe as `sid` (or, RC6.2, an
        add_markets / get_snapshot chunk is being sent on it): the markets
        it carries are known before their snapshots arrive. A market
        CURRENT on another sid keeps that sid.

        (RC6.2 review) On the ack that starts an acknowledged subscription
        the venue holds every market in `tickers` from its `subscribed` on
        (VENUE MEMBERSHIP; hold_subscribed adds the markets the subscribe
        named that were dropped while it awaited its ack). Only a market the
        venue holds can be made CURRENT by a snapshot (_acked_data).

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
                                     "n_cmds": 0, "cmd_index": {},
                                     "answered": {}, "gaps": 0,
                                     "sent_gaps": {},
                                     "ended": None, "awaiting": set(),
                                     "known": {t: (0, True) for t in tickers},
                                     "pend": {}, "cmds": {}, "expect": {},
                                     "unheld_snap": set(), "unsure": set()}
                self.recover.pop(sid, None)
                self.refused.pop(sid, None)
                self.readd_lost.pop(sid, None)
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
            self.legacy_separate.discard(sid)
            # (RC6.2 acceptance model, P1b per subscription) nothing the
            # old subscription carried is associated with the new one
            for t, s in list(self.ticker_sid.items()):
                if s == sid:
                    self.ticker_sid.pop(t, None)

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
        still outstanding asks the subscriber to end the session.

        (RC6.2 review) Named for a snapshot: only the markets the venue
        holds -- never one it refused, does not list or has an unanswered
        add / delete for (get_snapshot answers for any market, held or not,
        "without modifying the subscription": no delta would follow). A
        market whose add is unanswered is named for ONE re-add instead
        (`readd_lost`): the lost frame may be that add's reply or its own
        snapshot, and only a reply settles whether the venue holds it; the
        reply that confirms it then asks for its snapshot (`expect` is
        dropped, so _confirm does)."""
        if a["awaiting"]:
            self.fatal = self.fatal or R_GAP_DURING_RECOVERY
        self.stats["gaps"] += 1
        a["gaps"] += 1
        last = self.sid_seq.get(sid) or 0
        if data:
            a["last_data"] = max(a["last_data"], seq)
        if a["mode"] == SEPARATE:
            self.sid_seq[sid] = a["last_data"]
        else:
            self.sid_seq[sid] = max(last, seq)
        need = self.recover.setdefault(sid, set())
        for t in sorted(self.sid_markets.get(sid, ())):
            b = self.books.get(t)
            if b is None or t in self.forgotten or \
                    self._held_elsewhere(t, b, sid):
                continue
            if b["state"] == CURRENT:
                self._touch(t, b, state=GAP, why=why)
            self.resubscribe.add(t)
            if self._held(a, t):
                need.add(t)
            elif self._pending_add(a, t):
                self.readd_lost.setdefault(sid, set()).add(t)
                self.stats["readd_after_gap"] += 1
                a["expect"].pop(t, None)

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
        # nothing is associated with an ended subscription any more: the
        # number may be reused by another (acceptance model, P1b per
        # subscription)
        self.sid_markets.pop(sid, None)
        for t, s in list(self.ticker_sid.items()):
            if s == sid:
                self.ticker_sid.pop(t, None)
        self.sid_seq[sid] = None
        self.dead_sids.add(sid)
        self.fatal = self.fatal or R_SUBSCRIPTION_ENDED

    # ── venue membership of an acknowledged subscription (RC6.2 review) ──
    #
    # What the venue holds on the sid, from its own replies: `known[t]` is
    # (command id, held) -- t's state right after that command, by the
    # latest reply that spoke about it (our `subscribed` counts as command
    # 0, holding every market the subscribe named) -- and `pend[t]` the
    # add / delete commands naming t the venue has not answered. t is HELD
    # when the known state holds it and no delete of ours after that state
    # is unanswered (an add never removes a market: one the venue holds is
    # "no action"); only then can a snapshot of t make its book CURRENT.

    @staticmethod
    def _held(a, t) -> bool:
        k = a["known"].get(t)
        if k is None or not k[1]:
            return False
        return not any(act == "delete_markets" and c > k[0]
                       for c, act in a["pend"].get(t, ()))

    @staticmethod
    def _pending_add(a, t) -> bool:
        return any(act == "add_markets" for _c, act in a["pend"].get(t, ()))

    def _settle(self, a, sid, cid, typ, m, code=None) -> None:
        """The venue's reply (`ok` / `error`) to our add / delete `cid`.
        The `ok` carries the subscription's FULL ticker list after the
        update (asyncapi OK Response, msg.market_tickers): every market's
        state after `cid` is known -- held exactly when listed -- and every
        command up to `cid` is reflected in it. An `ok` without a list
        speaks for its own command (an add held its markets, a delete
        removed them).

        (Round 4) AN ERROR SAYS NOTHING ABOUT MEMBERSHIP. No error code is
        documented to mean "the command was not run" (error 18 is "Server
        timed out while processing command": the delete may well have run;
        error 27 is "exceeded its command rate limit", not that nothing
        happened), so no error is read as "the venue is as it was" -- round
        3 did, and a market whose delete the venue answered by an error
        after taking it out was taken as still held: re-wanted, its
        get_snapshot (answered for any market, "without modifying the
        subscription") made it CURRENT with no delta ever following. An
        errored DELETE leaves the market not held and UNSURE until a reply
        with the full list says (`_unknown_effect`); an errored ADD leaves a
        market not already held UNSURE (an add never removes a market). The
        command is just no longer pending (so a refused re-add of a market
        whose earlier add the venue confirmed leaves it held). Each command
        is settled once: a repeated reply is ignored."""
        info = a["cmds"].pop(cid, None) if type(cid) is int else None
        if info is None:
            return
        action, ts = info
        msg = m.get("msg") if isinstance(m.get("msg"), dict) else {}
        listed = msg.get("market_tickers") if typ == "ok" else None
        if isinstance(listed, list):
            on = set(listed)
            touched = set(a["known"]) | set(a["pend"]) | on
        else:
            touched = set(ts)
        for t in sorted(touched):
            was = self._held(a, t)
            p = a["pend"].get(t, [])
            if isinstance(listed, list):
                left = [x for x in p if x[0] > cid]
                if a["known"].get(t, (-1, False))[0] <= cid:
                    a["known"][t] = (cid, t in on)
                    a["unsure"].discard(t)
            else:
                left = [x for x in p if x[0] != cid]
                if typ == "ok" and a["known"].get(t, (-1, False))[0] <= cid:
                    a["known"][t] = (cid, action == "add_markets")
                    a["unsure"].discard(t)
                elif typ == "error" and cid in (x[0] for x in p):
                    self._unknown_effect(a, t, cid, action)
            answered = len(left) != len(p)
            if left:
                a["pend"][t] = left
                if answered and typ == "error" and \
                        action == "delete_markets":
                    # our delete answered by an error: it may or may not
                    # have run, so the add after it may find t held --
                    # "no action", no snapshot of its own -- or take it:
                    # its own snapshot is not to be waited for (its
                    # confirmation asks for one by get_snapshot)
                    a["expect"].pop(t, None)
            else:
                a["pend"].pop(t, None)
            now = self._held(a, t)
            if now and not was:
                self._confirm(a, sid, t, cid)
            elif not now and not left and (was or answered):
                self._refuse(a, sid, t, code if typ == "error" else None)

    def _unknown_effect(self, a, t, cid, action) -> None:
        """(Round 4) Our add / delete `cid` of t was answered by an ERROR:
        what the venue holds is unknown (see _settle). A later reply that
        already spoke about t stands (`known` carries the command id it
        speaks for). A delete: t is no longer known held, and may or may not
        be on the subscription. An add: a market not known held may be on
        the subscription now (an add of a held market changes nothing).
        Either way t is UNSURE: not held, so no snapshot of it is applied
        and no get_snapshot names it, until a reply with the full list
        settles it; wanted again it is added ALONE (an add of a held market
        is "no action", answered by an `ok` listing it; of one not held, it
        takes it and sends its own snapshot) and its confirmation asks for a
        snapshot (no own snapshot is expected for an unsure market)."""
        k = a["known"].get(t)
        if k is not None and k[0] > cid:
            return
        if action == "delete_markets":
            a["known"][t] = (cid, False)
            a["unsure"].add(t)
            self.stats["membership_unknown_after_error"] += 1
        elif k is None or not k[1]:
            a["unsure"].add(t)
            self.stats["membership_unknown_after_error"] += 1

    def _confirm(self, a, sid, t, cid) -> None:
        """The venue holds t on the subscription (settled by the reply to
        `cid`). A book that is not CURRENT gets a snapshot: the add's own
        -- only when this is the add's own reply, the add found t not held
        and no snapshot of t came before the reply -- or else a
        get_snapshot (unless one is already outstanding)."""
        exp = a["expect"].pop(t, None)
        seen = t in a["unheld_snap"]
        a["unheld_snap"].discard(t)
        b = self.books.get(t)
        if b is None or t in self.forgotten:
            return
        if b["state"] == CURRENT and b["sid"] is not None:
            return                  # served here, or on another live sid
        if t in a["awaiting"]:
            return
        # (round 3) a market not bound to the sid -- dropped and wanted
        # again while this add was in flight -- does not wait on the add's
        # own snapshot: a gap could not name it if that snapshot were lost
        # (get_snapshot binds it when sent)
        if seen or exp != cid or t not in self.sid_markets.get(sid, ()):
            self.recover.setdefault(sid, set()).add(t)

    def _refuse(self, a, sid, t, code) -> None:
        """The venue does not hold t on the subscription (it refused the
        add, its list lacks t, or our delete took it out), and nothing
        naming t is unanswered: no snapshot of t on it is applied until a
        reply holds it again; a wanted book is GAP (R_NOT_HELD_BY_VENUE),
        nothing is outstanding for it, and the subscriber is told
        (`refused`, with the error code if any)."""
        a["expect"].pop(t, None)
        a["unheld_snap"].discard(t)
        b = self.books.get(t)
        if b is None or t in self.forgotten:
            return
        self.stats["not_held_by_venue"] += 1
        a["awaiting"].discard(t)
        need = self.recover.get(sid)
        if need is not None:
            need.discard(t)
        lost = self.readd_lost.get(sid)
        if lost is not None:
            lost.discard(t)
        ms = self.sid_markets.get(sid)
        if self._current_elsewhere(b, sid):
            # served by another live subscription: nothing to repair here
            if ms is not None:
                ms.discard(t)
            return
        self._touch(t, b, state=GAP, why=R_NOT_HELD_BY_VENUE)
        self.resubscribe.add(t)
        if ms is not None:
            ms.discard(t)
        if self.ticker_sid.get(t) == sid:
            self.ticker_sid.pop(t, None)
        self.refused.setdefault(sid, {})[t] = code

    def adding(self, sid, t) -> bool:
        """Is an add naming t on `sid` unanswered?"""
        a = self.anchors.get(sid)
        return a is not None and self._pending_add(a, t)

    def unanswered(self, sid) -> dict:
        """(RC6.2 review) The add / delete commands on `sid` the venue has
        not answered: id -> (action, tickers)."""
        a = self.anchors.get(sid)
        return {} if a is None or a["ended"] else a["cmds"]

    def reply_lost(self, sid, cid) -> list:
        """(RC6.2 review) Our add `cid` on `sid` went unanswered past
        REPLY_TIMEOUT_S: its reply is taken as lost, as at a gap -- every
        market still waiting on it is named for a re-add (`readd_lost`) and
        no longer expects the add's own snapshot; returns them. (A late
        reply is still the venue's word: it settles what it says. A delete
        whose reply is lost stays unanswered: until a later reply settles
        it the market is not held.)"""
        a = self.anchors.get(sid)
        if a is None or a["ended"]:
            return []
        info = a["cmds"].get(cid)
        if info is None or info[0] != "add_markets":
            return []
        out = []
        for t in info[1]:
            if (cid, "add_markets") in a["pend"].get(t, ()) and \
                    t not in self.forgotten and t in self.books:
                self.readd_lost.setdefault(sid, set()).add(t)
                a["expect"].pop(t, None)
                out.append(t)
        return out

    def replace_add(self, sid, t) -> None:
        """(RC6.2 review) t's unanswered add on `sid` is taken over by the
        re-add about to be sent after a gap (its reply may have been the
        lost frame): t no longer waits on it. A later `ok` with the full
        list still settles t; an error to the re-add then refuses t (and
        the bounded re-add / rate retry follows) instead of leaving it
        waiting for ever on a reply that was lost. Safe: t is held only if
        the venue's latest reply said so and nothing naming t is
        unanswered -- an add of a market the venue holds is "no action"."""
        a = self.anchors.get(sid)
        if a is None:
            return
        p = [x for x in a["pend"].get(t, ()) if x[1] != "add_markets"]
        if p:
            a["pend"][t] = p
        else:
            a["pend"].pop(t, None)
        # the venue may hold t already (the lost reply an `ok`): the re-add
        # may be "no action", with no snapshot of its own to wait for
        a["unsure"].add(t)

    def unsure(self, sid, t) -> bool:
        """(round 3) May the venue hold t on `sid` although no reply said so
        (the reply to an add of it was taken as lost)?"""
        a = self.anchors.get(sid)
        return a is not None and not a["ended"] and t in a["unsure"]

    def held(self, sid, t) -> bool:
        """Does the venue hold t on the live acknowledged `sid` (its replies
        say so, and nothing naming t is unanswered)?"""
        a = self.anchors.get(sid)
        return a is not None and not a["ended"] and self._held(a, t)

    def may_hold(self, sid, t) -> bool:
        """(Round 4) May the venue hold t on the live acknowledged `sid`:
        its replies say so (held), or a command of ours naming t was
        answered by an error and no reply with the full list has settled it
        since (unsure)? Only a cleanup asks this (a dropped market is
        deleted again); a book is never made CURRENT by it -- `held` alone
        does that."""
        a = self.anchors.get(sid)
        return a is not None and not a["ended"] and (
            self._held(a, t) or t in a["unsure"])

    def hold_subscribed(self, sid, tickers) -> None:
        """(RC6.2 review) The `subscribed` answering our subscribe: the
        venue holds every market the subscribe NAMED -- also one dropped
        while it awaited its ack (bind leaves that one unbound; its
        delete_markets follows). Assumption H0 (module docstring)."""
        a = self.anchors.get(sid)
        if a is None or a["ended"]:
            return
        for t in tickers:
            if t not in a["pend"] and t not in a["known"]:
                a["known"][t] = (0, True)

    @staticmethod
    def _ctrl_bound(a, cid) -> int:
        """(RC6.2 review, round 3) The highest seq a SEPARATE control counter
        could carry on the control frame answering our update command `cid`
        on this subscription (an unknown or missing id: every update command
        sent on it) -- assumptions C1 / C2 (module docstring): the
        subscription's own `subscribed` may have taken a number (it carries
        no seq: whether it counted cannot be seen), each of our j update
        commands up to `cid` at most one more (C1; replies in command order,
        C2), and at most CONTROL_SEQ_SLACK the venue sends unasked (C1). It
        used to be j + CONTROL_SEQ_SLACK: with the `subscribed` and one
        unasked frame both numbered, the unasked frame landed on the shared
        slot and a delta lost after it was hidden."""
        j = a["cmd_index"].get(cid, a["n_cmds"]) if type(cid) is int \
            else a["n_cmds"]
        return 1 + j + CONTROL_SEQ_SLACK

    @staticmethod
    def _answered(a, cid) -> bool:
        """(RC6.2 review, round 3) Did a reply to our update command `cid`
        arrive before this one? Records this one (ids of our update commands
        on the sid only, at most MAX_CMD_INDEX)."""
        if type(cid) is not int or cid not in a["cmd_index"]:
            return False
        if cid in a["answered"]:
            return True
        a["answered"][cid] = True
        while len(a["answered"]) > MAX_CMD_INDEX:
            a["answered"].pop(next(iter(a["answered"])))
        return False

    def _control_seq(self, a, sid, seq, cid=None, *, dup=False) -> str:
        """Where a control frame's seq sits in an acknowledged sid's
        sequence (module docstring, THE SEQUENCE RULE FOR CONTROL FRAMES).
        `cid`: the command it answers (_ctrl_bound); `dup`: a reply to that
        command arrived before (this one replays it)."""
        if seq is None:
            return "NO_SEQ"
        bound = self._ctrl_bound(a, cid)
        if a["mode"] == SEPARATE:
            if seq > bound:
                # no separate counter reaches it (C1 / C2): the control
                # frames are in the shared sequence after all. What was lost
                # meanwhile cannot be known (control frames were not
                # sequenced): a gap, and the sid is SHARED again from here
                a["mode"] = SHARED
                self.stats["control_seq_shared_again"] += 1
                return "GAP"
            if seq > a["last_data"] + 1 and self._current_on(sid):
                # past the data run's next slot with a book CURRENT: what a
                # shared counter shows after a lost frame (a separate one
                # only on a sid that carried fewer data than control frames)
                # -- a gap, never a loss left unseen until the next data
                # frame (round 3)
                return "AMBIGUOUS"
            self.stats["control_ignored"] += 1
            return "IGNORED"
        last = self.sid_seq.get(sid) or 0
        if seq == last + 1:
            if seq > bound or not self._current_on(sid):
                self.sid_seq[sid] = seq
                self.stats["control_consumed"] += 1
                return "CONSUMED"
            return "AMBIGUOUS"
        if seq <= last:
            # A shared counter gives the FIRST reply to our command a seq
            # above every frame read before it (one ordered connection) --
            # unless a frame was lost after the command went out (then the
            # reply may be a late copy of the lost one, and a gap followed).
            # So the first reply to a command of ours, with no gap on the sid
            # since it was sent, at or below the bound, proves a separate
            # counter. Anything else at or below the last seq -- a second
            # reply, a frame answering no command we know, one after a gap,
            # one above the bound -- is a REPLAY of the shared sequence: a
            # gap while a book of the sid is CURRENT (a break, as a replayed
            # data frame is), counted otherwise; never a switch (round 3).
            first = (type(cid) is int and cid in a["cmd_index"] and not dup
                     and a["sent_gaps"].get(cid) == a["gaps"])
            if not first or seq > bound:
                self.stats["control_replayed"] += 1
                if self._current_on(sid):
                    return "REPLAYED"
                self.stats["control_ignored"] += 1
                return "IGNORED"
            a["mode"] = SEPARATE
            self.sid_seq[sid] = a["last_data"]
            self.stats["control_seq_separate"] += 1
            self.stats["control_ignored"] += 1
            return "SEPARATE"
        return "GAP"

    def _legacy_control_seq(self, sid, seq) -> str:
        """(RC6.2 review) A control frame (`ok`, a non-terminal scoped
        error) on a sid never acknowledged as ours, by THE SEQUENCE RULE FOR
        CONTROL FRAMES with the bound 1 + every command sent on this
        connection + CONTROL_SEQ_SLACK (each answered at most once; the
        sid's own announcement may have taken a number -- round 3): seq ==
        last + 1 is the next slot -- taken while no book of the sid is
        CURRENT (nothing to hide) or when the seq is past the bound; at or
        under it with a CURRENT book it is a gap (ambiguous); past last + 1
        is a gap. Seq <= last: no command of ours is matched on a number
        that is not ours, so it is a replay of the shared sequence while a
        book of the sid is CURRENT (a gap, round 3: it used to switch the
        sid to separate); with none CURRENT the sid's control frames are
        counted, never sequenced, from then on (separate) -- and on such a
        sid a control frame above the bound (shared after all) or past the
        data run's next slot with a book CURRENT (a shared counter's loss)
        is a gap. Such a sid DIES on a gap (the RC6 rule; no command is ever
        sent for it). RC6.1 ignored the frame without advancing the sid, so
        the next delta was a false gap (contract probe 1). With no sequence
        yet (nothing but deltas ahead of the first snapshot) the frame is
        not in it."""
        last = self.sid_seq.get(sid)
        bound = 1 + self.conn_cmds + CONTROL_SEQ_SLACK
        if seq is None or last is None:
            self.stats["control_ignored"] += 1
            return "IGNORED"
        if sid in self.legacy_separate:
            if seq > bound or (seq > last + 1 and self._current_on(sid)):
                self.legacy_separate.discard(sid)
                if seq > bound:
                    self.stats["control_seq_shared_again"] += 1
                self._gap_sid(sid, R_SEQ_GAP if seq > bound
                              else R_CONTROL_SEQUENCE_AMBIGUOUS)
                return "GAP"
            self.stats["control_ignored"] += 1
            return "IGNORED"
        if seq == last + 1:
            if seq > bound or not self._current_on(sid):
                self.sid_seq[sid] = seq
                self.stats["control_consumed"] += 1
                return "CONSUMED"
            self._gap_sid(sid, R_CONTROL_SEQUENCE_AMBIGUOUS)
            return "GAP"
        if seq <= last:
            if self._current_on(sid) or seq > bound:
                self.stats["control_replayed"] += 1
                if self._current_on(sid):
                    self._gap_sid(sid, R_SEQ_GAP)
                    return "GAP"
                self.stats["control_ignored"] += 1
                return "IGNORED"
            self.legacy_separate.add(sid)
            self.stats["control_seq_separate"] += 1
            self.stats["control_ignored"] += 1
            return "SEPARATE"
        self._gap_sid(sid, R_SEQ_GAP)
        return "GAP"

    def _data_seq(self, a, sid, seq) -> bool:
        """Is a data frame the acknowledged sid's next? (advances it)"""
        last = self.sid_seq.get(sid) or 0
        if a["mode"] == SEPARATE:
            ok = seq == a["last_data"] + 1
        elif seq == last + 1:
            ok = True
        elif seq == a["last_data"] + 1 and last > a["last_data"]:
            # the data run continues exactly over the slots control frames
            # took (or skipped, at a control frame's gap) since the last data
            # frame: a shared counter never gives a data frame a control
            # frame's seq, and a frame lost before a later one never arrives
            # after it -- their seq is a separate counter's (round 3: also
            # after an ambiguous control frame's gap, whose books are GAP)
            a["mode"] = SEPARATE
            self.stats["control_seq_separate"] += 1
            ok = True
        else:
            ok = False
        if ok:
            a["last_data"] = seq
            self.sid_seq[sid] = seq
        return ok

    def _control(self, m, typ, sid) -> str:
        self.stats["control_frames"] += 1
        code = (m.get("msg") or {}).get("code") if typ == "error" else None
        if typ == "error":
            self.stats["errors"] += 1
            self.stats["errors_terminal" if code in TERMINAL_ERROR_CODES
                       else "errors_nonterminal"] += 1
        cid = m.get("id")
        a = self.anchors.get(sid) if sid is not None else None
        if a is None and sid is None and typ == "error":
            # (RC6.2 review) an error without a sid ("present when the error
            # is scoped to a subscription"): a 10 / 25 ends what this
            # connection holds -- ONE subscription -- fail closed; any
            # other answers our command by its id
            if code in TERMINAL_ERROR_CODES:
                return self._end_unscoped()
            for s, x in self.anchors.items():
                if not x["ended"] and type(cid) is int and (
                        cid in x["cmds"] or cid in x["cmd_index"]):
                    # (round 3) its reply: a later one is a replay
                    self._answered(x, cid)
                    self._settle(x, s, cid, "error", m, code)
                    break
            return "ERROR"
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
                else:
                    # (RC6.2 review, contract probe 1) its `ok` / scoped
                    # error is in its sequence too, by the same rule
                    out = self._legacy_control_seq(sid, m.get("seq"))
                    if out == "GAP":
                        return "GAP"
                    if out == "CONSUMED":
                        return "ERROR" if typ == "error" else "OK"
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
        # (RC6.2 review) what the venue holds, from its reply to our command
        # (a frame after a loss is still the venue's own word on it)
        self._settle(a, sid, cid, typ, m, code)
        seq = m.get("seq")
        out = self._control_seq(a, sid, seq, cid,
                                dup=self._answered(a, cid))
        if out in ("GAP", "AMBIGUOUS", "REPLAYED"):
            self._gap_acked(a, sid, R_CONTROL_SEQUENCE_AMBIGUOUS
                            if out == "AMBIGUOUS" else R_SEQ_GAP, seq,
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
        if typ == SNAP and a["pend"].get(t) and not self._held(a, t):
            # (RC6.2 review) a snapshot of t while an add / delete naming it
            # is unanswered and the venue is not known to hold it -- maybe
            # the add's own (the order of an add's `ok` and its snapshots is
            # not documented): once a reply confirms t, a fresh one is asked
            # for (_confirm). Also while t is not tracked: it may be wanted
            # again before that reply.
            a["unheld_snap"].add(t)
        b = self.books.get(t)
        if b is None or t in self.forgotten:
            # a market we no longer track (or never did): its seq is kept
            self.stats["ignored_not_tracked"] += 1
            return "IGNORED_NOT_TRACKED"
        if typ == SNAP:
            # its request, if any, is answered
            a["awaiting"].discard(t)
            if not self._held(a, t):
                # (RC6.2 review) the venue is not known to hold t on the
                # sid: get_snapshot answers for any market, held or not
                # ("without adding them to the subscription"), and a
                # snapshot sent before our delete may arrive after the
                # market is wanted again -- no delta would follow either.
                # In the sequence, never applied; once a reply confirms t,
                # a fresh snapshot is asked for (_confirm)
                self.stats["snapshots_not_held"] += 1
                return "IGNORED_NOT_HELD"
            self._apply_snapshot(t, b, sid, seq, msg, m, at)
            return "SNAPSHOT"
        return self._apply_delta(t, b, sid, seq, msg, m, at)

    def _end_unscoped(self) -> str:
        """(RC6.2 review) error 10 / 25 without a sid: every live
        subscription of the connection ends (it holds ONE)."""
        live = sorted((s for s, x in self.anchors.items() if not x["ended"]),
                      key=str)
        for s in live:
            self._end(s, R_SUB_ERROR)
        others = sorted({s for s in list(self.sid_seq) + list(
            self.sid_markets) if s not in self.anchors
            and s not in self.dead_sids}, key=str)
        for s in others:
            self._gap_sid(s, R_SUB_ERROR)
        if live or others:
            self.fatal = self.fatal or R_SUBSCRIPTION_ENDED
        return "ERROR"

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
        # CURRENT: no snapshot is to be asked or awaited for it on any sid
        for a in self.anchors.values():
            a["awaiting"].discard(t)
        for need in self.recover.values():
            need.discard(t)
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
    def command_out(self) -> None:
        """(RC6.2 review) A command of any kind left on this connection."""
        self.conn_cmds += 1

    def command_sent(self, sid, cid=None, action=None, tickers=()) -> None:
        """An update command (id `cid`) was sent on an acknowledged sid.
        (RC6.2 review) An add / delete (`action`, `tickers`) puts its
        markets in motion: until the venue answers it, the venue may or may
        not hold them, so no snapshot of them is applied. An add expects
        the market's own snapshot only when the venue does not hold it
        (not confirmed, or a delete of ours goes first)."""
        a = self.anchors.get(sid)
        if a is None:
            return
        a["n_cmds"] += 1
        if cid is None:
            return
        a["cmd_index"][cid] = a["n_cmds"]
        a["sent_gaps"][cid] = a["gaps"]
        while len(a["cmd_index"]) > MAX_CMD_INDEX:
            a["cmd_index"].pop(next(iter(a["cmd_index"])))
        while len(a["sent_gaps"]) > MAX_CMD_INDEX:
            a["sent_gaps"].pop(next(iter(a["sent_gaps"])))
        if action not in ("add_markets", "delete_markets"):
            return
        a["cmds"][cid] = (action, tuple(tickers))
        while len(a["cmds"]) > MAX_CMD_INDEX:
            a["cmds"].pop(next(iter(a["cmds"])))
        for t in tickers:
            p = a["pend"].setdefault(t, [])
            # an add expects the market's own snapshot when the venue will
            # not hold it before the add: not known held with nothing in
            # flight, or our delete goes first
            fresh = (p[-1][1] == "delete_markets") if p else (
                not a["known"].get(t, (0, False))[1]
                and t not in a["unsure"])
            p.append((cid, action))
            a["unheld_snap"].discard(t)
            a["expect"].pop(t, None)
            if action == "add_markets" and fresh:
                a["expect"][t] = cid

    def requested(self, sid, tickers) -> None:
        """A get_snapshot naming `tickers` was sent on `sid`: outstanding
        until each book is CURRENT again (or dropped)."""
        a = self.anchors.get(sid)
        if a is not None:
            a["awaiting"].update(t for t in tickers if t in self.books)

    def unrequest(self, sid, tickers) -> None:
        """A get_snapshot naming `tickers` on `sid` was refused: nothing is
        outstanding for them any more; those not CURRENT are named again
        (RC6.2 review: only while the venue holds them -- one whose add is
        unanswered is asked for when a reply confirms it)."""
        a = self.anchors.get(sid)
        if a is None or a["ended"]:
            return
        need = self.recover.setdefault(sid, set())
        for t in tickers:
            a["awaiting"].discard(t)
            b = self.books.get(t)
            if b is not None and t not in self.forgotten and not (
                    b["state"] == CURRENT and b["sid"] == sid) and \
                    self._held(a, t):
                need.add(t)

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
                                           "awaiting": len(a["awaiting"]),
                                           "held_by_venue": sum(
                                               1 for t in a["known"]
                                               if self._held(a, t)),
                                           "unanswered": len(a["pend"])}
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


class ReplyTimeout(SessionEnd):
    """(RC6.2 review) A market's add went unanswered past REPLY_TIMEOUT_S
    after its re-adds (MAX_READDS_PER_MARKET) were spent: whether the venue
    holds it cannot be learned on this connection -- the reconnect
    subscribes afresh."""

    code = R_REPLY_TIMEOUT


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
        #: re-adds per market this session: after a refusal (at most one)
        #: and after a gap that may have lost the add's reply (at most
        #: MAX_READDS_PER_MARKET) -- two budgets, so a market whose gap
        #: re-adds were spent is still added again once when refused
        self.readds: dict = {}
        self.gap_readds: dict = {}
        self.unrecovered = {}
        #: (RC6.2 review) every market our one subscribe named (the venue
        #: holds them all once it answers `subscribed`)
        self.sub_tickers: list = []
        #: (RC6.2 review) market -> when its add is retried after the venue
        #: refused it twice for the command rate (error 27); retries so far
        self.rate_retry: dict = {}
        self.rate_retries: dict = {}
        #: (RC6.2 review) add / delete command id -> when it was sent (its
        #: reply's timeout, REPLY_TIMEOUT_S)
        self.sent_at: dict = {}
        #: (RC6.2 review, round 3) delete_markets id -> the markets it named
        #: (until the venue answers it); a market whose delete the venue
        #: refused while it is still dropped -> when its delete is sent
        #: again (RATE_LIMIT_RETRY_S later, at most MAX_RATE_LIMIT_RETRIES
        #: times a session: delete_retries)
        self.deleting: dict = {}
        self.delete_retry: dict = {}
        self.delete_retries: dict = {}
        #: (round 3) markets to re-add ALONE: refused while the reply to an
        #: earlier add of them may have been lost (the venue may hold them;
        #: a refusal of the whole command -- error 26 -- for another market
        #: named with them hid it)
        self.solo_readd: set = set()
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
        self.solo_readd.difference_update(gone)
        for t in gone:
            self.rate_retry.pop(t, None)
            # (round 4) the refusal budgets are per WANT: a market wanted
            # again after a drop starts afresh. They were per session, and a
            # market refused in an earlier want (by 18 or 27 once the venue
            # may have run the add anyway, or not taken) found its re-add
            # and its retries spent when the venue then HELD it: it stayed
            # GAP, streamed and ignored, for the rest of the session. Still
            # bounded by what the worker asks for: a refusal costs at most
            # one re-add and MAX_RATE_LIMIT_RETRIES retries per want.
            self.readds.pop(t, None)
            self.rate_retries.pop(t, None)
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
        if kind in ("add_markets", "delete_markets"):
            self.sent_at[cmd["id"]] = now
            while len(self.sent_at) > MAX_CMD_INDEX:
                self.sent_at.pop(next(iter(self.sent_at)))
        self.books.command_out()
        if sid is not None:
            self.books.command_sent(sid, cmd["id"], kind,
                                    cmd["params"].get("market_tickers") or ())
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
                self.delete_retry.pop(t, None)
                if t in self.queued_delete:
                    # dropped and wanted again before its delete went out:
                    # the venue still holds it; only its snapshot is needed
                    self.queued_delete.discard(t)
                    self.rewant.append(t)
                elif self.sid is not None and self.books.held(self.sid, t):
                    # dropped and wanted again while the venue's own replies
                    # say it holds the market (a reply with the full list
                    # that lists it, nothing of ours naming it unanswered):
                    # an add would be "no action" with no snapshot (the book
                    # waited for ever) -- its snapshot is asked for; a later
                    # drop deletes it again. (Round 4) NEVER on the strength
                    # of an errored delete: an error says nothing about
                    # membership, the market is UNSURE then, not held (see
                    # WsBooks._settle) and takes the branch below
                    self.on_venue.setdefault(t, 0)
                    self.rewant.append(t)
                else:
                    self.queued_add.append(t)
                    if self.sid is not None and self.books.unsure(self.sid, t):
                        # (round 4) the venue may hold it already (an
                        # errored delete or add of it): added ALONE -- an add
                        # of a held market is "no action", answered by `ok`
                        # with the full list, which an error 26 for another
                        # market in the same command could hide
                        self.solo_readd.add(t)
        if self.sub_id is None:
            first = sorted(t for t in self.queued_add
                           if t not in self.books.forgotten)
            if first:
                c = next(iter(chunks(first)))
                self.queued_add = first[len(c):]
                cmd = self.cmd.subscribe(c)
                self.sub_id = cmd["id"]
                self.sub_tickers = list(c)
                self.pending[cmd["id"]] = list(c)
                for t in c:
                    self.on_venue[t] = cmd["id"]
                await self._send(ws, cmd)
                sent += 1
        sid = self.sid
        if sid is not None and self.rewant:
            # (RC6.2 review) a market dropped and wanted again before its
            # delete went out, by what the venue holds -- held: its snapshot
            # (get_snapshot below); its add unanswered: the reply settles it
            # (a confirmation asks for its snapshot); neither (its add was
            # refused or not taken while it was dropped): added again
            keep = []
            for t in self.rewant:
                if t not in self.books.books or t in self.books.forgotten:
                    continue
                if self.books.held(sid, t):
                    keep.append(t)
                elif not self.books.adding(sid, t):
                    self.queued_add.append(t)
            self.rewant = keep
        if sid is not None and self.sent_at:
            self._reply_timeouts(sid)
        if sid is not None:
            if self.delete_retry:
                # (round 3) a refused delete, sent again once due -- only
                # while the market is still dropped and the venue holds it
                now = self.clock()
                for t in sorted(self.delete_retry):
                    if self.delete_retry[t] <= now:
                        del self.delete_retry[t]
                        if (t not in self.books.books or
                                t in self.books.forgotten) and \
                                self.books.may_hold(sid, t):
                            self.queued_delete.add(t)
            if self.queued_delete:
                ts = sorted(self.queued_delete)
                self.queued_delete = set()
                for c in chunks(ts):
                    cmd = self.cmd.update_subscription(sid, "delete_markets",
                                                       c)
                    self.pending[cmd["id"]] = []
                    self.deleting[cmd["id"]] = list(c)
                    while len(self.deleting) > MAX_CMD_INDEX:
                        self.deleting.pop(next(iter(self.deleting)))
                    for t in c:
                        self.on_venue.pop(t, None)
                    await self._send(ws, cmd, sid)
                    sent += 1
            if self.rate_retry:
                now = self.clock()
                for t in sorted(self.rate_retry):
                    if self.rate_retry[t] <= now:
                        del self.rate_retry[t]
                        if t in self.books.books and \
                                t not in self.books.forgotten:
                            self.readd.append(t)
                            if self.books.unsure(sid, t):
                                self.solo_readd.add(t)
            adds = sorted(set(self.queued_add) | set(self.readd)) if (
                self.queued_add or self.readd) else ()
            self.queued_add, self.readd = [], []
            # (round 3) a market the venue may hold already (an add's reply
            # taken as lost) that was refused goes ALONE: an add of a market
            # held is "no action", answered by `ok` with the full list -- a
            # refusal of the whole command for another market (error 26, the
            # market limit) can no longer hide that the venue holds it
            solo = [t for t in adds if t in self.solo_readd]
            self.solo_readd.difference_update(solo)
            rest = [t for t in adds if t not in solo]
            for c in [[t] for t in solo] + list(chunks(rest)):
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
                # (RC6.2 review) only a market the venue holds: get_snapshot
                # answers for any market "without modifying the
                # subscription", and no delta follows a snapshot of one it
                # does not hold (one whose add is unanswered is asked for
                # when a reply confirms it)
                need = sorted(t for t in want_snap if t in self.books.books
                              and t not in self.books.forgotten
                              and t not in outstanding
                              and self.books.held(sid, t))
            if need:
                if gap:
                    self.resubscribes += 1
                for c in chunks(need):
                    c = [t for t in c if t not in self.books.forgotten
                         and t in self.books.books
                         and self.books.held(sid, t)]
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
                self.books.hold_subscribed(sid, self.sub_tickers)
            else:
                self.books.bind(sid, [], ours=False)
            return self.books.on_message(m, recv_at=self.clock())
        if self.sid is None and self.sub_id is not None and \
                cid == self.sub_id and typ in ("ok", "error"):
            # our one subscribe answered by anything but `subscribed`
            self.books.fatal = self.books.fatal or R_SUBSCRIBE_REFUSED
        out = self.books.on_message(m, recv_at=self.clock())
        if typ == "ok":
            if type(cid) is int:
                self.pending.pop(cid, None)
                self.deleting.pop(cid, None)
        elif typ == "error" and type(cid) is int:
            self._refused(cid, m)
        elif out == "SNAPSHOT":
            # its book is CURRENT again: its repairs converged
            self.unrecovered.pop((m.get("msg") or {}).get("market_ticker"),
                                 None)
        self._readds()
        return out

    def _readds(self) -> None:
        """(RC6.2 review) What the books learned from the venue's replies
        (WsBooks._settle) and from a gap, turned into re-adds, bounded: a
        market the venue refused or did not list is added again ONCE (a
        second refusal leaves it GAP, not held -- after error 27, the
        command rate, or 18, a timeout (the add may even have run: round 4),
        it is retried RATE_LIMIT_RETRY_S later, at most
        MAX_RATE_LIMIT_RETRIES times); a market whose add was unanswered
        at a gap is added again (the lost frame may be its reply), within
        MAX_READDS_PER_MARKET (its own budget: a refusal after those still
        re-adds once). Hard bounds per market and session: 1 + 2 + 3
        re-adds, and the command rate (they are not snapshot requests: the
        plane-hang and per-market get_snapshot bounds count get_snapshot
        only, so a market refused once and then caught by a gap is not a
        "storm"). A listed market we no longer want has no book, so it is
        never CURRENT."""
        refused = self.books.refused
        lost = self.books.readd_lost
        if not refused and not lost:
            return
        mine = refused.pop(self.sid, {}) if self.sid is not None else {}
        gap = lost.pop(self.sid, set()) if self.sid is not None else set()
        refused.clear()
        lost.clear()
        for t in sorted(mine):
            if t not in self.books.books or t in self.books.forgotten:
                continue
            self.on_venue.pop(t, None)
            n = self.readds.get(t, 0)
            if n < 1:
                self.readds[t] = n + 1
                self.readd.append(t)
                if self.books.unsure(self.sid, t):
                    # (round 3) the venue may hold it already: re-added
                    # alone (see _commands)
                    self.solo_readd.add(t)
            elif mine[t] in RETRY_LATER_ERROR_CODES and \
                    self.rate_retries.get(t, 0) < MAX_RATE_LIMIT_RETRIES:
                self.rate_retries[t] = self.rate_retries.get(t, 0) + 1
                self.rate_retry[t] = self.clock() + RATE_LIMIT_RETRY_S
        for t in sorted(gap):
            if t not in self.books.books or t in self.books.forgotten or \
                    not self.books.adding(self.sid, t):
                continue
            n = self.gap_readds.get(t, 0)
            if n < MAX_READDS_PER_MARKET:
                self.gap_readds[t] = n + 1
                self.books.replace_add(self.sid, t)
                self.readd.append(t)

    def _reply_timeouts(self, sid) -> None:
        """(RC6.2 review) An add the venue has not answered REPLY_TIMEOUT_S
        after it was sent: its reply is taken as lost (a sid that carries
        nothing else would never show the loss) -- its markets are re-added
        within the gap re-add budget; a market whose budget is spent ends
        the session (ReplyTimeout: fail closed, the reconnect subscribes
        afresh). Never a loop: each command times out once."""
        now = self.clock()
        open_ = self.books.unanswered(sid)
        for cid, at in list(self.sent_at.items()):
            if cid not in open_:
                del self.sent_at[cid]
                continue
            if now - at < REPLY_TIMEOUT_S:
                continue
            del self.sent_at[cid]
            for t in self.books.reply_lost(sid, cid):
                if self.gap_readds.get(t, 0) >= MAX_READDS_PER_MARKET:
                    self._end(ReplyTimeout, "%s unanswered %.0f s after its "
                              "re-adds" % (t, now - at))
        self._readds()

    def next_due(self):
        """(RC6.2 review) When the session next acts with nothing read (a
        rate-limit retry, a reply's timeout), or None."""
        due = list(self.rate_retry.values()) + list(
            self.delete_retry.values())
        if self.sid is not None:
            open_ = self.books.unanswered(self.sid)
            due += [at + REPLY_TIMEOUT_S for cid, at in self.sent_at.items()
                    if cid in open_]
        return min(due) if due else None

    def _refused(self, cid, m: dict) -> None:
        """An `error` answering one of our update commands (counted; only
        10 / 25 are fatal, and the books handled those; a refused add /
        delete is settled by the books and re-added by _readds): the
        markets a refused get_snapshot named will get no snapshot from it
        -- they are asked again (bounded: the per-market and plane-hang
        bounds end the session past their limits)."""
        named = self.pending.pop(cid, None) or []
        dels = self.deleting.pop(cid, None) or []
        kind = self.cmd_kind.get(cid)
        code = (m.get("msg") or {}).get("code")
        if self.sid is None or code in TERMINAL_ERROR_CODES:
            return
        if kind == "delete_markets" and dels:
            self._delete_refused(dels)
            return
        if not named:
            return
        if kind == "get_snapshot":
            self.books.unrequest(self.sid, [
                t for t in named if t in self.books.books
                and t not in self.books.forgotten])

    def _delete_refused(self, tickers) -> None:
        """(RC6.2 review, round 3; round 4) Our delete_markets answered by an
        error (27, the per-subscription command rate, 18, the server's
        timeout, or any other): whether the venue took the markets out is
        UNKNOWN -- an error says nothing about membership (WsBooks._settle:
        round 3 took "the venue still holds them" and a market whose delete
        had in fact run was re-wanted and served stale). A market wanted
        again meanwhile is added again ALONE by the wanted-set step (its
        `ok` lists it, held or taken, and its confirmation asks for the
        snapshot). One still dropped that the venue may hold is deleted
        again RATE_LIMIT_RETRY_S later (a retry inside the same rate window
        would be refused again), at most MAX_RATE_LIMIT_RETRIES times a
        session; never a loop (each refusal schedules at most one retry).
        It used to stay on the venue for the rest of the session, and when
        wanted again its add was "no action": no snapshot, the book waited
        for ever."""
        now = self.clock()
        for t in tickers:
            if t in self.books.books and t not in self.books.forgotten:
                continue
            if not self.books.may_hold(self.sid, t):
                continue
            n = self.delete_retries.get(t, 0)
            if n >= MAX_RATE_LIMIT_RETRIES:
                continue
            self.delete_retries[t] = n + 1
            self.delete_retry[t] = now + RATE_LIMIT_RETRY_S

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
        """Sessions until `stop`; between two, the backoff. (RC6.2 review)
        A session only ever ends by raising, so the backoff index never
        went back to its first step: after six ends in the life of the
        process every reconnect waited the last step. A session that lasted
        BACKOFF_RESET_AFTER_S is not a reconnect loop: the next wait starts
        from the first step again."""
        i = 0
        while stop is None or not stop.is_set():
            started = self.clock()
            try:
                await self.session()
                i = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:                            # noqa: BLE001
                self.last_error = type(exc).__name__
                try:
                    lasted = float(self.clock()) - float(started)
                except (TypeError, ValueError):
                    lasted = 0.0
                if lasted >= BACKOFF_RESET_AFTER_S:
                    i = 0
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
