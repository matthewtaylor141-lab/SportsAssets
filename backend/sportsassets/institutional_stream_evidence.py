"""DURABLE RUNTIME EVIDENCE OF THE INSTITUTIONAL gRPC STREAM (workers process).

WHAT THIS IS. The stream (`institutional_stream`) keeps resident books in the
memory of the process that runs it -- the workers service, the only service
holding PMX_*. Its heartbeat digest says how it is doing NOW and is
overwritten every beat. This module makes the stream's behaviour DURABLE and
readable from another process (the API's P5 evaluation, the readback SQL):

  * a LISTENER on `ResidentBooks` (`books.listener`) that sees every
    connect, disconnect, refusal, heartbeat, ack, subscription error and
    update the transport delivers, and accumulates per-minute facts;
  * `rows()` -- one row per WANTED SYMBOL per minute, plus one process row
    (symbol '*'): connection identity and epoch, connect / reconnect times,
    the first complete book on the current epoch after the (re)connect, gap
    events opened and closed in the minute, venue transact_time against our
    receipt (receipt age at the flush instant, venue/receipt skew and its
    min / median / max over the minute's updates), message and update
    counts, the largest gap between consecutive updates, the instrument
    state, the top-N levels held, and the stream's own `current()` answer
    with its full evidence (so P5 can be re-evaluated on it later);
  * `persist()` -- an idempotent upsert into `institutional_stream_evidence`
    (migration 210), keyed (process_id, symbol, minute); counters ADD on a
    second flush in the same minute.

WHAT IT IS NOT. Not a decision input and not REST polling: it records only
what the gRPC stream delivered. It is written only when the stream is enabled
(INSTITUTIONAL_MD_STREAM=on) in the process that runs it. It never sees a
token or a credential value: the books never hold one.

THREADING. The listener is called by `ResidentBooks` INSIDE the books' lock
(from the transport's stream thread); it takes only its own lock and never
calls back into the books. `rows()` reads the books first (their lock,
released) and then its own state -- one lock order, no inversion.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

VERSION = "INSTITUTIONAL_STREAM_EVIDENCE_V1"
TABLE = "institutional_stream_evidence"
PROCESS_SYMBOL = "*"
TOP_N = 5
#: Gap events kept per symbol per minute (a flapping stream is still bounded).
MAX_GAP_EVENTS = 20
#: Skew samples kept per symbol per minute (median over a bounded sample).
MAX_SKEW_SAMPLES = 600

#: THIS process's identity: service, host, pid and a boot id. Not a secret.
BOOT_ID = uuid.uuid4().hex[:12]


def process_id(service: str) -> str:
    host = (os.environ.get("RENDER_INSTANCE_ID")
            or os.environ.get("HOSTNAME") or socket.gethostname() or "host")
    return "%s:%s:%d:%s" % (service, str(host)[:64], os.getpid(), BOOT_ID)


def _ts(v):
    """epoch seconds / aware datetime -> aware datetime (or None)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else None
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _epoch(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.timestamp() if v.tzinfo else None
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d.timestamp() if d.tzinfo else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def plain(d: Decimal) -> str:
    """An exact decimal as plain text: 10, 0.45 -- never 1E+1."""
    return format(d.normalize(), "f")


def scaled_levels(levels, ps, qs, n=TOP_N) -> list:
    """[(px int, qty int)] -> [{"price": "0.45", "size": "10"}] by the
    instrument's OWN scales, exactly (Decimal). Unscaled when a scale is
    unknown -- never priced with an assumed scale."""
    out = []
    for p, q in list(levels or ())[:n]:
        row = {"px": int(p), "qty": int(q)}
        try:
            if ps:
                row["price"] = plain(Decimal(int(p)) / Decimal(int(ps)))
            if qs:
                row["size"] = plain(Decimal(int(q)) / Decimal(int(qs)))
        except (InvalidOperation, ValueError, ZeroDivisionError):
            pass
        out.append(row)
    return out


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = len(xs) // 2
    return xs[k] if len(xs) % 2 else (xs[k - 1] + xs[k]) / 2.0


class _Sym:
    __slots__ = ("updates_total", "updates_min", "regressions_min",
                 "stray_min", "skews", "gap_events", "first_book",
                 "last_update_at", "max_interarrival_s", "levels",
                 "stream_state")

    def __init__(self):
        self.updates_total = 0
        self.updates_min = 0
        self.regressions_min = 0
        self.stray_min = 0
        self.skews: list = []
        self.gap_events: list = []
        #: {epoch seq: receipt instant of the FIRST complete book on it}
        self.first_book: dict = {}
        self.last_update_at = None
        self.max_interarrival_s = None
        self.levels = None
        self.stream_state = None


class StreamEvidence:
    """The listener plus the per-minute accumulator. Thread-safe."""

    def __init__(self, *, service: str = "institutional_md", clock=time.time):
        self.service = service
        self.process_id = process_id(service)
        self._clock = clock
        self._lock = threading.Lock()
        self._sym: dict = {}
        self.connects_total = 0
        self.disconnects_total = 0
        self.refusals_total = 0
        self.connects_min = 0
        self.disconnects_min = 0
        self.messages_total = 0
        self.messages_min = 0
        self.heartbeats_min = 0
        self.acks_min = 0
        self.sub_errors_min: list = []
        self.first_connect_at = None
        self.last_connect_at = None
        self.last_connect_seq = None
        self.conn_id = None
        self.last_disconnect_at = None
        self.last_disconnect_why = None
        self.last_refusal = None
        self.connect_events_min: list = []

    # ── the listener (called inside the books' lock) ──────────────────

    def __call__(self, event: str, data: dict) -> None:
        with self._lock:
            at = data.get("at")
            if event == "connected":
                self.connects_total += 1
                self.connects_min += 1
                self.first_connect_at = self.first_connect_at or at
                self.last_connect_at = at
                self.last_connect_seq = data.get("seq")
                self.conn_id = data.get("conn_id")
                self.connect_events_min.append(
                    {"event": "connect", "at": _r(at),
                     "seq": data.get("seq"), "conn_id": data.get("conn_id")})
                return
            if event in ("disconnected", "refused", "gave_up"):
                if event == "disconnected":
                    self.disconnects_total += 1
                    self.disconnects_min += 1
                    self.last_disconnect_at = at
                    self.last_disconnect_why = data.get("why")
                elif event == "refused":
                    self.refusals_total += 1
                    self.last_refusal = {"code": data.get("code"),
                                         "at": _r(at)}
                self.connect_events_min.append(
                    {"event": event, "at": _r(at), "seq": data.get("seq"),
                     "why": data.get("why") or data.get("code")
                     or data.get("failures")})
                del self.connect_events_min[:-MAX_GAP_EVENTS]
                for s in data.get("gapped") or ():
                    self._gap(s, {"event": "GAP_OPENED",
                                  "reason": "CONNECTION_" + event.upper(),
                                  "at": _r(at), "seq": data.get("seq")})
                return
            self.messages_total += 1
            self.messages_min += 1
            if event == "heartbeat":
                self.heartbeats_min += 1
                return
            if event == "ack":
                self.acks_min += 1
                return
            if event == "subscription_error":
                self.sub_errors_min.append({"code": data.get("code"),
                                            "at": _r(at)})
                del self.sub_errors_min[:-MAX_GAP_EVENTS]
                return
            if event != "update":
                return
            sym = str(data.get("symbol") or "")
            st = self._sym.setdefault(sym, _Sym())
            if data.get("stray"):
                st.stray_min += 1
                return
            if data.get("regression"):
                st.regressions_min += 1
                self._gap(sym, {"event": "GAP_OPENED",
                                "reason": "VENUE_CLOCK_WENT_BACKWARDS",
                                "at": _r(at), "seq": data.get("seq"),
                                "high_water": str(data.get("high_water"))})
                return
            if not data.get("accepted"):
                return
            st.updates_total += 1
            st.updates_min += 1
            seq = data.get("seq")
            if seq is not None and seq not in st.first_book:
                st.first_book[seq] = at
                for old in [k for k in st.first_book if k != seq][:-2]:
                    st.first_book.pop(old, None)
            if st.last_update_at is not None and at is not None:
                gap = at - st.last_update_at
                if st.max_interarrival_s is None or gap > st.max_interarrival_s:
                    st.max_interarrival_s = gap
            st.last_update_at = at
            vts = _epoch(data.get("venue_ts"))
            if vts is not None and at is not None \
                    and len(st.skews) < MAX_SKEW_SAMPLES:
                st.skews.append(at - vts)
            if data.get("closed_gap"):
                self._gap(sym, {"event": "GAP_CLOSED_BY_COMPLETE_BOOK",
                                "reason": data.get("closed_gap"),
                                "at": _r(at), "seq": seq})
            st.levels = data.get("levels")
            st.stream_state = data.get("state") or st.stream_state

    def _gap(self, sym, ev) -> None:
        st = self._sym.setdefault(str(sym), _Sym())
        st.gap_events.append(ev)
        del st.gap_events[:-MAX_GAP_EVENTS]

    # ── the per-minute rows ───────────────────────────────────────────

    def rows(self, books, *, now=None, identity_for=None) -> list:
        """One row per wanted symbol plus the process row, for the minute
        containing `now`; resets the minute counters. Reads the books FIRST
        (their own lock), then this accumulator."""
        at = float(now if now is not None else self._clock())
        minute = _ts(int(at // 60) * 60)
        wanted = books.wanted()
        reads, held = {}, {}
        for s in wanted:
            reads[s] = books.current(s, now=at)
            held[s] = books.held_levels(s, TOP_N)
        state, why = books.state, books.state_why
        with self._lock:
            base = {
                "minute": minute, "process_id": self.process_id,
                "service": self.service, "recorded_at": _ts(at),
                "evaluated_at_epoch": at, "version": VERSION,
                "stream_state": state, "stream_state_why": str(why)[:300],
                "connection_id": self.conn_id,
                "connection_epoch": self.last_connect_seq,
                "connects_total": self.connects_total,
                "reconnects_total": max(0, self.connects_total - 1),
                "connects_in_minute": self.connects_min,
                "disconnects_in_minute": self.disconnects_min,
                "first_connect_at": _ts(self.first_connect_at),
                "last_connect_at": _ts(self.last_connect_at),
                "last_disconnect_at": _ts(self.last_disconnect_at),
                "last_disconnect_why": self.last_disconnect_why,
                "messages_total": self.messages_total,
                "messages_in_minute": self.messages_min,
            }
            proc = dict(base, symbol=PROCESS_SYMBOL, extra={
                "heartbeats_in_minute": self.heartbeats_min,
                "acks_in_minute": self.acks_min,
                "subscription_errors_in_minute": list(self.sub_errors_min),
                "connection_events_in_minute": list(self.connect_events_min),
                "refusals_total": self.refusals_total,
                "last_refusal": self.last_refusal,
                "wanted_symbols": len(wanted)})
            out = [proc]
            for s in wanted:
                st = self._sym.setdefault(s, _Sym())
                rd = reads[s]
                ev = rd.get("evidence") or {}
                conn = ev.get("connection") or {}
                snap = ev.get("snapshot") or {}
                mkt = ev.get("market") or {}
                recv = snap.get("received_at")
                vts = _epoch(snap.get("venue_ts"))
                seq = conn.get("seq")
                first = st.first_book.get(seq) if seq is not None else None
                conn_at = conn.get("connected_at")
                hl = held[s]
                ps, qs = hl.get("price_scale"), hl.get("qty_scale")
                ident = None
                if identity_for is not None:
                    try:
                        ident = identity_for(s)
                    except Exception:                         # noqa: BLE001
                        ident = None
                out.append(dict(
                    base, symbol=s,
                    connection_id=conn.get("id") or self.conn_id,
                    connection_epoch=seq,
                    connected=conn.get("connected"),
                    connected_at=_ts(conn_at),
                    first_complete_book_at=_ts(first),
                    first_complete_book_after_connect_s=(
                        _r(first - conn_at) if (first is not None
                                                and conn_at is not None)
                        else None),
                    gap_open=(ev.get("gap") or {}).get("reason"),
                    gap_events=list(st.gap_events),
                    regressions_total=ev.get("regressions"),
                    regressions_in_minute=st.regressions_min,
                    updates_total=st.updates_total,
                    updates_in_minute=st.updates_min,
                    stray_in_minute=st.stray_min,
                    max_interarrival_s=_r(st.max_interarrival_s),
                    venue_ts=_ts(vts),
                    received_at=_ts(recv),
                    receipt_age_s=_r(None if recv is None else at - recv),
                    venue_receipt_skew_s=_r(
                        None if (recv is None or vts is None)
                        else recv - vts),
                    skew_min_s=_r(min(st.skews) if st.skews else None),
                    skew_p50_s=_r(_median(st.skews)),
                    skew_max_s=_r(max(st.skews) if st.skews else None),
                    skew_samples=len(st.skews),
                    instrument_state=mkt.get("state"),
                    state_source=mkt.get("state_source"),
                    price_scale=ps, qty_scale=qs,
                    depth_bids=len(hl.get("bids") or ()),
                    depth_offers=len(hl.get("offers") or ()),
                    top_n={"n": TOP_N, "book_epoch": hl.get("book_seq"),
                           "bids": scaled_levels(hl.get("bids"), ps, qs),
                           "offers": scaled_levels(hl.get("offers"), ps, qs)},
                    current_ok=bool(rd.get("ok")),
                    current_refusal=rd.get("refusal"),
                    current_read={"ok": rd.get("ok"),
                                  "symbol": rd.get("symbol"),
                                  "refusal": rd.get("refusal"),
                                  "why": rd.get("why"),
                                  "book_present": rd.get("book") is not None,
                                  "evidence": ev},
                    identity=ident,
                    extra={}))
                # reset the minute
                st.updates_min = st.regressions_min = st.stray_min = 0
                st.skews = []
                st.gap_events = []
                st.max_interarrival_s = None
            self.connects_min = self.disconnects_min = 0
            self.messages_min = self.heartbeats_min = self.acks_min = 0
            self.sub_errors_min = []
            self.connect_events_min = []
        return out


# ── persistence (migration 210) ───────────────────────────────────────

COLUMNS = (
    "minute", "symbol", "process_id", "service", "recorded_at",
    "evaluated_at_epoch", "version", "stream_state", "stream_state_why",
    "connection_id", "connection_epoch", "connected", "connected_at",
    "connects_total", "reconnects_total", "connects_in_minute",
    "disconnects_in_minute", "first_connect_at", "last_connect_at",
    "last_disconnect_at", "last_disconnect_why",
    "first_complete_book_at", "first_complete_book_after_connect_s",
    "gap_open", "gap_events", "regressions_total", "regressions_in_minute",
    "messages_total", "messages_in_minute", "updates_total",
    "updates_in_minute", "stray_in_minute", "max_interarrival_s",
    "venue_ts", "received_at", "receipt_age_s", "venue_receipt_skew_s",
    "skew_min_s", "skew_p50_s", "skew_max_s", "skew_samples",
    "instrument_state", "state_source", "price_scale", "qty_scale",
    "depth_bids", "depth_offers", "top_n", "current_ok", "current_refusal",
    "current_read", "identity", "extra")
JSON_COLUMNS = ("gap_events", "top_n", "current_read", "identity", "extra")
#: counters that ADD when one minute is flushed twice; gap events append;
#: the extremes widen; everything else is the latest flush's value.
_ADD = ("connects_in_minute", "disconnects_in_minute",
        "regressions_in_minute", "messages_in_minute", "updates_in_minute",
        "stray_in_minute", "skew_samples")


def _upsert_sql() -> str:
    cols = ", ".join(COLUMNS)
    vals = ", ".join("$%d%s" % (i + 1, "::jsonb" if c in JSON_COLUMNS else "")
                     for i, c in enumerate(COLUMNS))
    sets = []
    for c in COLUMNS:
        if c in ("minute", "symbol", "process_id"):
            continue
        if c in _ADD:
            sets.append("%s = COALESCE(t.%s, 0) + COALESCE(EXCLUDED.%s, 0)"
                        % (c, c, c))
        elif c == "gap_events":
            sets.append("gap_events = COALESCE(t.gap_events, '[]'::jsonb) "
                        "|| COALESCE(EXCLUDED.gap_events, '[]'::jsonb)")
        elif c == "skew_min_s":
            sets.append("skew_min_s = LEAST(t.skew_min_s, EXCLUDED.skew_min_s)")
        elif c in ("skew_max_s", "max_interarrival_s"):
            sets.append("%s = GREATEST(t.%s, EXCLUDED.%s)" % (c, c, c))
        else:
            sets.append("%s = EXCLUDED.%s" % (c, c))
    return ("INSERT INTO %s AS t (%s) VALUES (%s) "
            "ON CONFLICT (process_id, symbol, minute) DO UPDATE SET %s"
            % (TABLE, cols, vals, ", ".join(sets)))


UPSERT_SQL = _upsert_sql()


def _param(c, v):
    if c in JSON_COLUMNS:
        return json.dumps(v if v is not None else
                          ([] if c == "gap_events" else None), default=str)
    return v


async def persist(pool, rows) -> int:
    """Upsert the minute's rows. Returns rows written; never raises."""
    n = 0
    for r in rows or ():
        try:
            await pool.execute(UPSERT_SQL,
                               *[_param(c, r.get(c)) for c in COLUMNS])
            n += 1
        except Exception:                                     # noqa: BLE001
            continue
    return n


# ── the process's one recorder ────────────────────────────────────────

RECORDER: StreamEvidence | None = None


def install(books, *, service: str = "institutional_md") -> StreamEvidence:
    """Attach a recorder to `books` (the process's ResidentBooks). Idempotent
    per books object."""
    global RECORDER
    cur = getattr(books, "listener", None)
    if isinstance(cur, StreamEvidence):
        RECORDER = cur
        return cur
    RECORDER = StreamEvidence(service=service)
    books.listener = RECORDER
    return RECORDER


def stream_read_from_row(row: dict) -> dict | None:
    """Rebuild the stream's `current()` answer recorded on a row (for P5
    re-evaluation in another process). None when the row carries none."""
    cr = row.get("current_read") if isinstance(row, dict) else None
    if isinstance(cr, str):
        try:
            cr = json.loads(cr)
        except ValueError:
            return None
    if not isinstance(cr, dict) or not isinstance(cr.get("evidence"), dict):
        return None
    return {"ok": cr.get("ok") is True, "symbol": cr.get("symbol"),
            "refusal": cr.get("refusal"), "why": cr.get("why"),
            # the book itself is not stored, only that one was returned
            "book": {"recorded": True} if cr.get("book_present") else None,
            "evidence": cr["evidence"]}
