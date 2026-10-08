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

CURRENT only after a complete snapshot on the CURRENT subscription. A seq
that is not last + 1 on its sid, a disconnect, an error on the sid: every
book of that sid goes GAP at once, its later deltas are ignored, the
markets are resubscribed, and each book returns to CURRENT only when its
fresh snapshot arrives. A pre-gap book is never routed.

Account limits: GET /trade-api/v2/account/limits is the authoritative
usage tier and read / write token buckets; REST recovery pacing derives
from the read bucket. The schema returns no WebSocket connection cap, so
none is assumed (this runtime opens ONE connection).

STRUCTURALLY READ-ONLY: this module and its runtime import no order,
cancel, funding, Small Live or capital code (tests/test_kalshi_ws_market_
data.py checks the import closure). The credential is used only to sign
the handshake and the limits read.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from decimal import Decimal

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
SUBSCRIBE_CHUNK = 100

CURRENT = "CURRENT"
GAP = "GAP"
PENDING = "AWAITING_SNAPSHOT"
UNSUBSCRIBED = "UNSUBSCRIBED"

R_SEQ_GAP = "KALSHI_WS_SEQUENCE_GAP"
R_DISCONNECT = "KALSHI_WS_DISCONNECTED"
R_SUB_ERROR = "KALSHI_WS_SUBSCRIPTION_ERROR"
R_NO_CREDENTIAL = "KALSHI_WS_CREDENTIAL_NOT_PROVISIONED"


# ── signing (the handshake and the limits read) ─────────────────────────

def load_private_key(pem: str):
    from cryptography.hazmat.primitives import serialization
    text = str(pem or "").strip()
    if not text:
        raise ValueError(R_NO_CREDENTIAL)
    if "-----BEGIN" not in text:
        try:
            text = base64.b64decode(text).decode("utf-8")
        except Exception as exc:                                # noqa: BLE001
            raise ValueError("KALSHI_KEY_NOT_PEM") from exc
    return serialization.load_pem_private_key(text.encode(), password=None)


def signing_message(ts_ms: str, method: str, path: str) -> bytes:
    """timestamp + METHOD + path without the query (kalshi_venue's rule)."""
    return (str(ts_ms) + str(method).upper() + str(path).split("?")[0]
            ).encode("utf-8")


def sign(private_key, ts_ms: str, method: str, path: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    msg = signing_message(ts_ms, method, path)
    if isinstance(private_key, Ed25519PrivateKey):
        sig = private_key.sign(msg)
    else:
        sig = private_key.sign(msg, padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


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

def _d(x) -> Decimal:
    return Decimal(str(x))


class WsBooks:
    """Per-market books under per-sid sequence discipline."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.books: dict = {}       # ticker -> {yes, no, sid, state, ...}
        self.sid_seq: dict = {}     # sid -> last seq
        self.sid_markets: dict = {}  # sid -> set(tickers)
        self.ticker_sid: dict = {}
        self.stats = {"snapshots": 0, "deltas": 0, "gaps": 0,
                      "disconnects": 0, "ignored_after_gap": 0,
                      "errors": 0}
        self.resubscribe: set = set()
        self.connected = False

    def _book(self, t):
        return self.books.setdefault(t, {
            "yes": {}, "no": {}, "sid": None, "state": PENDING,
            "snapshot_at": None, "updated_at": None, "venue_ts_ms": None,
            "seq": None, "why": None})

    def want(self, tickers) -> None:
        for t in tickers:
            self._book(t)

    def on_connected(self) -> None:
        self.connected = True

    def on_disconnected(self) -> None:
        """Every book GAP: a book held across a reconnect is never reused."""
        self.connected = False
        self.stats["disconnects"] += 1
        for t, b in self.books.items():
            if b["state"] != UNSUBSCRIBED:
                b.update(state=GAP, why=R_DISCONNECT, sid=None)
                self.resubscribe.add(t)
        self.sid_seq.clear()
        self.sid_markets.clear()
        self.ticker_sid.clear()

    def _gap_sid(self, sid, why) -> None:
        self.stats["gaps"] += 1
        for t in self.sid_markets.get(sid, set()):
            b = self._book(t)
            b.update(state=GAP, why=why)
            self.resubscribe.add(t)
        self.sid_seq[sid] = None

    def on_message(self, m: dict, *, recv_at: float | None = None) -> str:
        """Apply one decoded WS message; returns what happened."""
        at = float(recv_at if recv_at is not None else self.clock())
        typ = m.get("type")
        sid = m.get("sid")
        if typ == "subscribed":
            return "SUBSCRIBED"
        if typ == "error":
            self.stats["errors"] += 1
            if sid is not None:
                self._gap_sid(sid, R_SUB_ERROR)
            return "ERROR"
        if typ not in ("orderbook_snapshot", "orderbook_delta"):
            return "IGNORED"
        msg = m.get("msg") or {}
        t = msg.get("market_ticker")
        seq = m.get("seq")
        if t is None or sid is None or seq is None:
            return "MALFORMED"
        if typ == "orderbook_snapshot":
            b = self._book(t)
            yes = {_d(p): _d(q) for p, q in msg.get("yes_dollars_fp") or []}
            no = {_d(p): _d(q) for p, q in msg.get("no_dollars_fp") or []}
            b.update(yes={p: q for p, q in yes.items() if q > 0},
                     no={p: q for p, q in no.items() if q > 0},
                     sid=sid, state=CURRENT, snapshot_at=at, updated_at=at,
                     venue_ts_ms=m.get("sending_ts_ms"), seq=seq, why=None)
            self.sid_markets.setdefault(sid, set()).add(t)
            self.ticker_sid[t] = sid
            self.sid_seq[sid] = seq
            self.resubscribe.discard(t)
            self.stats["snapshots"] += 1
            return "SNAPSHOT"
        # delta
        last = self.sid_seq.get(sid)
        b = self._book(t)
        if last is None or b["sid"] != sid or b["state"] != CURRENT:
            self.stats["ignored_after_gap"] += 1
            return "IGNORED_NOT_CURRENT"
        if seq != last + 1:
            self._gap_sid(sid, R_SEQ_GAP)
            return "GAP"
        self.sid_seq[sid] = seq
        side = str(msg.get("side") or "").lower()
        if side not in ("yes", "no"):
            self._gap_sid(sid, R_SUB_ERROR)
            return "GAP"
        lv = b[side]
        p, dq = _d(msg.get("price_dollars")), _d(msg.get("delta_fp"))
        q = lv.get(p, Decimal(0)) + dq
        if q > 0:
            lv[p] = q
        else:
            lv.pop(p, None)
        b.update(updated_at=at, seq=seq,
                 venue_ts_ms=msg.get("ts_ms") or m.get("sending_ts_ms"))
        self.stats["deltas"] += 1
        return "DELTA"

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
                "connected": self.connected, **self.stats}


# ── commands ─────────────────────────────────────────────────────────────

class Commands:
    def __init__(self):
        self.n = 0

    def _id(self):
        self.n += 1
        return self.n

    def subscribe(self, tickers: list) -> dict:
        return {"id": self._id(), "cmd": "subscribe", "params": {
            "channels": [CHANNEL], "market_tickers": list(tickers)}}

    def unsubscribe(self, sids: list) -> dict:
        return {"id": self._id(), "cmd": "unsubscribe",
                "params": {"sids": list(sids)}}


def chunks(xs: list, n: int = SUBSCRIBE_CHUNK):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# ── the runtime (one connection) ─────────────────────────────────────────

class Subscriber:
    """One authenticated connection: subscribe the wanted markets in
    chunks, apply messages, resubscribe on a gap, reconnect on a drop.
    `connect` is an async callable returning a socket with async send /
    recv / close (websockets in production, a fake in tests)."""

    def __init__(self, connect, books: WsBooks, *, wanted,
                 clock=time.time, backoff=(1, 2, 5, 10, 30, 60)):
        self.connect = connect
        self.books = books
        self.wanted = wanted            # callable -> list of tickers
        self.clock = clock
        self.backoff = backoff
        self.cmd = Commands()
        self.subscribed: set = set()
        self.connections = 0
        self.resubscribes = 0
        self.last_error = None

    async def _subscribe(self, ws, tickers) -> None:
        for c in chunks(sorted(tickers)):
            await ws.send(json.dumps(self.cmd.subscribe(c)))
            self.subscribed.update(c)

    async def _resubscribe_gapped(self, ws) -> None:
        todo = sorted(self.books.resubscribe)
        if not todo:
            return
        sids = sorted({self.books.ticker_sid.get(t) for t in todo
                       if self.books.ticker_sid.get(t) is not None})
        if sids:
            await ws.send(json.dumps(self.cmd.unsubscribe(sids)))
        for t in todo:
            self.books.ticker_sid.pop(t, None)
        self.resubscribes += 1
        await self._subscribe(ws, todo)
        # stay GAP until each fresh snapshot arrives; do not re-send
        self.books.resubscribe.difference_update(todo)

    async def session(self, *, max_messages: int | None = None) -> None:
        ws = await self.connect()
        self.connections += 1
        self.books.on_connected()
        try:
            want = list(self.wanted())
            self.books.want(want)
            self.subscribed = set()
            await self._subscribe(ws, want)
            n = 0
            while max_messages is None or n < max_messages:
                raw = await ws.recv()
                n += 1
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                self.books.on_message(m, recv_at=self.clock())
                if self.books.resubscribe:
                    await self._resubscribe_gapped(ws)
                add = [t for t in self.wanted() if t not in self.subscribed]
                if add:
                    self.books.want(add)
                    await self._subscribe(ws, add)
        finally:
            self.books.on_disconnected()
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
