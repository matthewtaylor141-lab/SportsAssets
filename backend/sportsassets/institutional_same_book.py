"""THE SAME-BOOK PROBE: IS THE INSTITUTIONAL STREAM BOOK THE RETAIL BOOK?

THE PREMISE UNDER TEST. `institutional_contract_map.BOOK_EQUIVALENCE` records
that the retail Polymarket US venue and the exchange run ONE central limit
order book -- by documentation, "NOT_EMPIRICALLY_VERIFIED": no simultaneous
read of both has been made, and the one side-by-side on record (2026-09-19,
institutional 0.97/0.99 vs retail 0.59/0.60) was never resolved. P5's C1
(exact contract identity, LB6) carries that claim (NE7). This module
produces the evidence for or against it.

WHAT ONE SAMPLE IS (read-only, workers process):

  1. identity: `institutional_contract_map.map_retail_to_institutional` for
     (retail slug, YES) against the institutional refdata record. Anything
     but an exact mapping -> NOT_COMPARABLE (no read is compared);
  2. stream read #1: the RESIDENT stream book, `books.current(symbol)`;
  3. retail read: ONE public GET of `/v1/markets/{slug}/book` on the retail
     gateway (the same documented read `pmus.book_read` makes through the SDK's
     `markets.book`), on a KEYLESS client whose transport refuses every
     method but GET (`ReadOnlyTransport`) and also passes the process write
     lock and pacing gate (`venue_request_gate.PacedTransport`);
  4. stream read #2: `books.current(symbol)` again.

  The pair is COMPARABLE only when both stream reads are current (the
  stream's own `current()` answered ok), the retail read returned a book,
  and the whole window (stream read #1 -> stream read #2) is <= 1 s. The
  retail book is compared, after scale conversion (px / priceScale,
  qty / fractionalQtyScale, exact Decimal), with the stream book before and
  after; the retail instant lies between them, so agreement with either
  counts and `matched_stream_read` says which.

VERDICTS. AGREE_TOP_N (best bid, best offer and the top-N levels -- price and
size -- equal); AGREE_TOUCH_ONLY (best bid and best offer prices equal,
deeper levels or touch sizes differ); DISAGREE (a best price differs);
NOT_COMPARABLE (with the reason). Persisted to `institutional_same_book_probe`
(migration 210), whose orders_placed is CHECKed to zero.

AGREEMENT EVIDENCE (migration 213). Every sample also carries, for its
row: the institutional symbol (only when the mapping is EXACT), the side, the
BETTOR event, institutional and retail best bid / ask as compared, the stream
epoch, the gap state, both receipt ages at the comparison instant, the
comparison timestamp, the agreement result (null when not comparable) and the
incomparable reason -- and, when the worker probes its focus universe
(institutional_focus_universe), the member's tier and why.

NO ORDER PATH. This module calls one thing on the retail client --
`markets.book` -- on a client built with no key, through a transport that
refuses non-GET. It imports no order module. A test pins all of that.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import institutional_contract_map as ICM

VERSION = "INSTITUTIONAL_SAME_BOOK_PROBE_V1"
TABLE = "institutional_same_book_probe"
TOP_N = 5
#: Both reads of one sample fall inside this window (seconds).
MAX_WINDOW_S = 1.0
RETAIL_BOOK_PATH = "/v1/markets/%s/book"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9.\-]{2,200}$")

V_AGREE = "AGREE_TOP_N"
V_TOUCH = "AGREE_TOUCH_ONLY"
V_DISAGREE = "DISAGREE"
V_NC = "NOT_COMPARABLE"
VERDICTS = (V_AGREE, V_TOUCH, V_DISAGREE, V_NC)

NC_IDENTITY = "IDENTITY_NOT_EXACT"
NC_STREAM = "STREAM_BOOK_NOT_CURRENT"
NC_RETAIL = "RETAIL_BOOK_UNREADABLE"
NC_WINDOW = "READS_NOT_WITHIN_WINDOW"
NC_EMPTY = "BOTH_BOOKS_EMPTY"
NC_SCALE = "INSTITUTIONAL_SCALES_UNKNOWN"


class WriteRefused(RuntimeError):
    """A non-GET request on the probe's retail client."""


def _ts(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else None
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _dec(v):
    if isinstance(v, dict):
        v = v.get("value")
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


# ── the two books, normalised to exact (price, size) ──────────────────

def stream_levels(read: dict) -> dict | None:
    """A current() answer -> {"bids": [(price, size)], "offers": [...]} by
    the instrument's OWN scales (exact). None when not current / unscaled."""
    if not (isinstance(read, dict) and read.get("ok") and read.get("book")):
        return None
    mkt = ((read.get("evidence") or {}).get("market") or {})
    ps, qs = mkt.get("price_scale"), mkt.get("qty_scale")
    if not (ps and qs):
        return None
    ps, qs = Decimal(int(ps)), Decimal(int(qs))
    out = {}
    for side in ("bids", "offers"):
        lv = []
        for e in read["book"].get(side) or ():
            try:
                lv.append((Decimal(int(e["px"])) / ps,
                           Decimal(int(e["qty"])) / qs))
            except (KeyError, TypeError, ValueError, InvalidOperation):
                return None
        out[side] = lv
    return _sorted(out)


def retail_levels(market_data: dict) -> dict | None:
    """The retail `marketData` -> the same shape. Levels read as published:
    {"px": {"value": "0.52"}, "qty": "25"} (or bare scalars). A level that
    does not parse makes the whole book unreadable -- never dropped."""
    if not isinstance(market_data, dict):
        return None
    out = {}
    for side, keys in (("bids", ("bids",)), ("offers", ("offers", "asks"))):
        raw = None
        for k in keys:
            if isinstance(market_data.get(k), list):
                raw = market_data[k]
                break
        lv = []
        for e in raw or ():
            if not isinstance(e, dict):
                return None
            p = _dec(e.get("px", e.get("price")))
            q = _dec(e.get("qty", e.get("size")))
            if p is None or q is None:
                return None
            lv.append((p, q))
        out[side] = lv
    return _sorted(out)


def _sorted(book: dict) -> dict:
    return {"bids": sorted(book.get("bids") or (), key=lambda x: -x[0]),
            "offers": sorted(book.get("offers") or (), key=lambda x: x[0])}


def _show(book, n=TOP_N):
    if book is None:
        return None
    return {s: [{"price": _plain(p), "size": _plain(q)}
                for p, q in (book.get(s) or ())[:n]]
            for s in ("bids", "offers")}


def compare(stream_book: dict | None, retail_book: dict | None,
            *, top_n: int = TOP_N) -> dict:
    """Pure: the agreement of two normalised books."""
    if stream_book is None or retail_book is None:
        return {"verdict": V_NC, "reason": NC_STREAM if stream_book is None
                else NC_RETAIL}
    if not any(stream_book.values()) and not any(retail_book.values()):
        return {"verdict": V_NC, "reason": NC_EMPTY}

    def best(b, side):
        lv = b.get(side) or ()
        return lv[0] if lv else None

    sb, so = best(stream_book, "bids"), best(stream_book, "offers")
    rb, ro = best(retail_book, "bids"), best(retail_book, "offers")
    bid_eq = (sb[0] if sb else None) == (rb[0] if rb else None)
    off_eq = (so[0] if so else None) == (ro[0] if ro else None)
    qty_eq = ((sb[1] if sb else None) == (rb[1] if rb else None)
              and (so[1] if so else None) == (ro[1] if ro else None))
    diff = []
    for side in ("bids", "offers"):
        a = list(stream_book.get(side) or ())[:top_n]
        b = list(retail_book.get(side) or ())[:top_n]
        for i in range(max(len(a), len(b))):
            x = a[i] if i < len(a) else None
            y = b[i] if i < len(b) else None
            if x != y:
                diff.append({"side": side, "level": i,
                             "stream": None if x is None else
                             [_plain(x[0]), _plain(x[1])],
                             "retail": None if y is None else
                             [_plain(y[0]), _plain(y[1])]})
    levels_eq = not diff
    if bid_eq and off_eq and levels_eq:
        v, why = V_AGREE, None
    elif bid_eq and off_eq:
        v, why = V_TOUCH, ("TOUCH_SIZE_DIFFERS" if not qty_eq
                           else "DEEPER_LEVELS_DIFFER")
    else:
        v, why = V_DISAGREE, ("BEST_BID_DIFFERS" if not bid_eq
                              else "BEST_OFFER_DIFFERS")
    return {"verdict": v, "reason": why, "best_bid_equal": bid_eq,
            "best_offer_equal": off_eq, "touch_qty_equal": qty_eq,
            "levels_equal": levels_eq, "diff": diff[:2 * top_n]}


_RANK = {V_AGREE: 0, V_TOUCH: 1, V_DISAGREE: 2, V_NC: 3}


def sample(symbol: str, *, record, retail_row=None, books_current,
           retail_read, clock=time.time, top_n: int = TOP_N,
           max_window_s: float = MAX_WINDOW_S, focus=None) -> dict:
    """ONE probe sample (see module doc). `books_current(symbol, now=)` is
    the stream's resident read; `retail_read(slug)` returns
    {"ok", "marketData", "error"}. `focus` (the focus-universe member, when
    the worker probes its universe) stamps the member's tier and why. Never
    raises. Every answer carries the agreement evidence (`agreement_fields`).
    """
    row = _sample(symbol, record=record, retail_row=retail_row,
                  books_current=books_current, retail_read=retail_read,
                  clock=clock, top_n=top_n, max_window_s=max_window_s)
    return agreement_fields(row, focus=focus, retail_row=retail_row)


def _top(book, side):
    lv = ((book or {}).get(side) or [])
    return _dec(lv[0].get("price")) if lv else None


def _age(later, earlier):
    if not (isinstance(later, datetime) and isinstance(earlier, datetime)):
        return None
    return round((later - earlier).total_seconds(), 4)


def agreement_fields(row: dict, *, focus=None, retail_row=None) -> dict:
    """Pure: the per-observation agreement evidence (migration 213) from one
    sample -- institutional symbol (only when the mapping is EXACT), side,
    event, institutional and retail best bid / ask (as compared, exact),
    stream epoch (connection_epoch), gap state, both receipt ages at the
    comparison instant, the comparison timestamp, the agreement result
    (None when not comparable) and the incomparable reason."""
    r = dict(row)
    ident = r.get("identity") or {}
    exact = bool(r.get("identity_ok")) and \
        ident.get("institutional_symbol") == r.get("symbol")
    f = focus if isinstance(focus, dict) else {}
    fi = f.get("identity") if isinstance(f.get("identity"), dict) else {}
    compared = r.get("compared_at") or r.get("probed_at")
    v = r.get("verdict")
    r.update(
        institutional_symbol=ident.get("institutional_symbol") if exact
        else None,
        identity_exact=exact,
        outcome_side="YES",
        bettor_event=f.get("bettor_event") or fi.get("bettor_event"),
        retail_event_slug=(fi.get("retail_event_slug")
                           or (retail_row or {}).get("event_slug")),
        inst_best_bid=_top(r.get("stream_book"), "bids"),
        inst_best_ask=_top(r.get("stream_book"), "offers"),
        retail_best_bid=_top(r.get("retail_book"), "bids"),
        retail_best_ask=_top(r.get("retail_book"), "offers"),
        inst_receipt_age_s=_age(compared, r.get("stream_received_at")),
        retail_receipt_age_s=_age(compared, r.get("retail_response_at")),
        compared_at=compared,
        agreed=(None if v == V_NC else v in (V_AGREE, V_TOUCH)),
        incomparable_reason=(r.get("verdict_reason") or "UNSTATED")
        if v == V_NC else None,
        focus_tier=f.get("tier"), focus_tier_rank=f.get("tier_rank"),
        focus_why=(str(f.get("why"))[:500] if f.get("why") else None),
        focus_universe_id=f.get("universe_id"))
    return r


def _sample(symbol: str, *, record, retail_row=None, books_current,
            retail_read, clock=time.time, top_n: int = TOP_N,
            max_window_s: float = MAX_WINDOW_S) -> dict:
    slug = str(symbol or "")
    base = {"version": VERSION, "symbol": slug, "retail_slug": slug,
            "top_n": top_n, "max_window_s": max_window_s, "orders_placed": 0}
    ident = ICM.map_retail_to_institutional(slug, "yes", record,
                                            retail_row=retail_row)
    base.update(identity_ok=bool(ident.get("ok")),
                identity_refusal=ident.get("refusal"),
                identity={k: ident.get(k) for k in (
                    "version", "ok", "refusal", "why", "institutional_symbol",
                    "institutional_side", "price_transform", "price_scale",
                    "qty_scale", "payout_value", "basis")},
                probed_at=_ts(clock()))
    if not ident.get("ok") or ident.get("institutional_symbol") != slug:
        return dict(base, verdict=V_NC, verdict_reason=NC_IDENTITY)
    try:
        t0 = clock()
        s1 = books_current(slug, now=t0)
        t_req = clock()
        r = retail_read(slug) or {}
        t_resp = clock()
        s2 = books_current(slug, now=t_resp)
        t1 = clock()
    except Exception as exc:                                  # noqa: BLE001
        return dict(base, verdict=V_NC,
                    verdict_reason="PROBE_RAISED:%s" % type(exc).__name__)
    window = t1 - t0
    ev2 = (s2 or {}).get("evidence") or {}
    conn = ev2.get("connection") or {}
    snap = ev2.get("snapshot") or {}
    b1, b2 = stream_levels(s1), stream_levels(s2)
    rb = retail_levels(r.get("marketData")) if r.get("ok") else None
    md = r.get("marketData") if isinstance(r.get("marketData"), dict) else {}
    out = dict(
        base, probed_at=_ts(t0),
        stream_ok=bool((s1 or {}).get("ok") and (s2 or {}).get("ok")),
        stream_refusal=(s2 or {}).get("refusal") or (s1 or {}).get("refusal"),
        connection_epoch=conn.get("seq"), connection_id=conn.get("id"),
        compared_at=_ts(t1),
        gap_state=((ev2.get("gap") or {}).get("reason")
                   if ev2.get("gap") else ("NO_GAP" if ev2 else None)),
        stream_received_at=_ts(snap.get("received_at")),
        stream_venue_ts=snap.get("venue_ts"),
        retail_ok=bool(r.get("ok")) and rb is not None,
        retail_error=r.get("error") or (None if rb is not None or not
                                        r.get("ok") else "BOOK_UNPARSEABLE"),
        retail_request_at=_ts(t_req), retail_response_at=_ts(t_resp),
        window_s=round(window, 4), within_window=window <= max_window_s,
        stream_changed_in_window=(b1 != b2),
        stream_book=_show(b2 if b2 is not None else b1, top_n),
        retail_book=dict(_show(rb, top_n) or {},
                         keys=sorted(str(k) for k in md)[:30],
                         transact_time=md.get("transactTime")
                         or md.get("transact_time")) if md else None)
    if b1 is None and b2 is None:
        why = NC_STREAM
        mkt = ((s2 or s1 or {}).get("evidence") or {}).get("market") or {}
        if (s2 or {}).get("ok") and not (mkt.get("price_scale")
                                         and mkt.get("qty_scale")):
            why = NC_SCALE
        return dict(out, verdict=V_NC, verdict_reason=why)
    if rb is None:
        return dict(out, verdict=V_NC, verdict_reason=NC_RETAIL)
    if window > max_window_s:
        return dict(out, verdict=V_NC, verdict_reason=NC_WINDOW)
    c_before = compare(b1, rb, top_n=top_n) if b1 is not None else None
    c_after = compare(b2, rb, top_n=top_n) if b2 is not None else None
    cands = [(n, c) for n, c in (("after", c_after), ("before", c_before))
             if c is not None]
    best_rank = min(_RANK[c["verdict"]] for _, c in cands)
    matched = [n for n, c in cands if _RANK[c["verdict"]] == best_rank]
    name, c = [(n, c) for n, c in cands if n == matched[0]][0]
    return dict(out, verdict=c["verdict"], verdict_reason=c.get("reason"),
                matched_stream_read=("both" if len(matched) == 2
                                     else matched[0])
                if c["verdict"] in (V_AGREE, V_TOUCH) else None,
                best_bid_equal=c.get("best_bid_equal"),
                best_offer_equal=c.get("best_offer_equal"),
                touch_qty_equal=c.get("touch_qty_equal"),
                levels_equal=c.get("levels_equal"),
                diff={"compared_with": name, "levels": c.get("diff") or []},
                stream_book=_show(b2 if name == "after" else b1, top_n))


# ── the retail read: keyless, GET-only, gated ─────────────────────────

class ReadOnlyTransport:
    """An httpx transport wrapper that REFUSES every method but GET/HEAD,
    before anything is sent, whatever called it."""

    def __init__(self, inner):
        self._inner = inner

    def handle_request(self, request):
        method = str(getattr(request, "method", "")).upper()
        if method not in ("GET", "HEAD"):
            raise WriteRefused("refused: %s is not a read" % method)
        return self._inner.handle_request(request)

    def close(self):
        try:
            self._inner.close()
        except Exception:                                     # noqa: BLE001
            pass


def install_read_only(client, *, inner=None, wrap_with=None):
    """Wrap EVERY transport the client's httpx client can route through --
    the default one and each proxy mount (an HTTPS_PROXY in the environment
    routes through a mount, not `_transport`) -- GET-only outermost, the
    process write lock + pacing gate (`venue_request_gate.PacedTransport`)
    inside. `inner` (tests) replaces the network transport everywhere.
    `wrap_with(t)` (the paper public-gateway lane) replaces the shared
    PacedTransport with the caller's own gate; GET-only stays outermost."""
    http = getattr(client, "_http", None)
    if http is None or getattr(http, "_transport", None) is None:
        raise RuntimeError("the retail SDK client exposes no transport")
    try:
        from . import venue_request_gate as _grt
        from .venue_pace import pace as _pace
        paced = _grt.PacedTransport
    except Exception:                                         # noqa: BLE001
        paced, _pace = None, None

    def wrap(t):
        t = inner if inner is not None else t
        if wrap_with is not None:
            t = wrap_with(t)
        elif paced is not None and inner is None:
            t = paced(t, pace=_pace)
        return ReadOnlyTransport(t)
    http._transport = wrap(http._transport)
    mounts = getattr(http, "_mounts", None)
    if isinstance(mounts, dict):
        for k, t in list(mounts.items()):
            if t is not None:
                mounts[k] = wrap(t)
    return client


def _keyless_client(*, wrap_with=None):
    """The retail SDK client with NO key (public gateway endpoints only),
    its own retries off, every transport wrapped read-only and gated.
    `wrap_with` (the paper public-gateway lane) supplies that lane's own
    book-only gate in place of the shared PacedTransport."""
    from polymarket_us import PolymarketUS
    try:
        from . import venue_sdk as _vsdk
        extra = _vsdk.client_kwargs()
    except Exception:                                         # noqa: BLE001
        extra = {}
    return install_read_only(PolymarketUS(**extra), wrap_with=wrap_with)


_CLIENT = None


def retail_book_read(slug: str, *, client=None) -> dict:
    """ONE public retail book read. {"ok", "marketData", "error"}. Never
    raises; never sends anything but GET /v1/markets/{slug}/book."""
    global _CLIENT
    s = str(slug or "")
    if not _SLUG.match(s):
        return {"ok": False, "marketData": None, "error": "SLUG_REFUSED"}
    try:
        c = client
        if c is None:
            if _CLIENT is None:
                _CLIENT = _keyless_client()
            c = _CLIENT
        payload = c.markets.book(s) or {}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "marketData": None,
                "error": type(exc).__name__}
    md = payload.get("marketData") if isinstance(payload, dict) else None
    if not isinstance(md, dict):
        return {"ok": False, "marketData": None,
                "error": "NO_MARKET_DATA_IN_PAYLOAD"}
    return {"ok": True, "marketData": md, "error": None}


# ── persistence (migration 210, + 213's agreement fields) ─────────────

COLUMNS_210 = (
    "probed_at", "process_id", "service", "version", "symbol", "retail_slug",
    "identity_ok", "identity_refusal", "identity", "stream_ok",
    "stream_refusal", "connection_epoch", "connection_id",
    "stream_received_at", "stream_venue_ts", "retail_ok", "retail_error",
    "retail_request_at", "retail_response_at", "window_s", "max_window_s",
    "within_window", "stream_changed_in_window", "matched_stream_read",
    "verdict", "verdict_reason", "top_n", "best_bid_equal",
    "best_offer_equal", "touch_qty_equal", "levels_equal", "stream_book",
    "retail_book", "diff")
#: Migration 213: every observation's agreement evidence and focus tier.
COLUMNS_213 = (
    "institutional_symbol", "identity_exact", "outcome_side", "bettor_event",
    "retail_event_slug", "inst_best_bid", "inst_best_ask", "retail_best_bid",
    "retail_best_ask", "gap_state", "inst_receipt_age_s",
    "retail_receipt_age_s", "compared_at", "agreed", "incomparable_reason",
    "focus_tier", "focus_tier_rank", "focus_why", "focus_universe_id")
COLUMNS = COLUMNS_210 + COLUMNS_213
JSON_COLUMNS = ("identity", "stream_book", "retail_book", "diff")


def _insert_sql(cols) -> str:
    return "INSERT INTO %s (%s) VALUES (%s)" % (
        TABLE, ", ".join(cols),
        ", ".join("$%d%s" % (i + 1, "::jsonb" if c in JSON_COLUMNS else "")
                  for i, c in enumerate(cols)))


INSERT_SQL = _insert_sql(COLUMNS)
INSERT_SQL_210 = _insert_sql(COLUMNS_210)


def _param(c, v):
    if c in JSON_COLUMNS:
        return json.dumps(v, default=str)
    if c == "stream_venue_ts" and isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if c == "probed_at" and v is None:
        return datetime.now(tz=timezone.utc)
    return v


def _missing_column(exc) -> bool:
    return type(exc).__name__ == "UndefinedColumnError"


HAS_213_SQL = ("SELECT 1 FROM information_schema.columns WHERE "
               "table_name = $1 AND column_name = 'focus_universe_id'")


async def _columns(pool) -> tuple:
    """213's columns when the table has them, else 210's (never raises)."""
    try:
        return COLUMNS if await pool.fetch(HAS_213_SQL, TABLE) else \
            COLUMNS_210
    except Exception:                                         # noqa: BLE001
        return COLUMNS


async def persist(pool, rows, *, process_id: str, service: str) -> int:
    """One row per sample. With migration 213 absent the 210 columns are
    written instead (checked first; an undefined column also falls back), so
    the evidence is never lost to a schema lag. Never raises."""
    n = 0
    cols = await _columns(pool) if rows else COLUMNS
    for r in rows or ():
        r = dict(r, process_id=process_id, service=service)
        sql = INSERT_SQL if cols is COLUMNS else INSERT_SQL_210
        try:
            await pool.execute(sql, *[_param(c, r.get(c)) for c in cols])
            n += 1
        except Exception as exc:                              # noqa: BLE001
            if cols is not COLUMNS or not _missing_column(exc):
                continue
            try:
                await pool.execute(INSERT_SQL_210, *[
                    _param(c, r.get(c)) for c in COLUMNS_210])
                n += 1
            except Exception:                                 # noqa: BLE001
                continue
    return n
