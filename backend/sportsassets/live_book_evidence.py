"""THE LIVE BOOK-CURRENCY EVIDENCE FOR ONE PAPER DECISION (decision path).

Installed by `execution_intent.start` as `decision_hooks.LIVE_BOOK_EVIDENCE`
(the paper modules never import this module). For the contract a qualified
decision is about, it

  1. asks the INJECTABLE identity mapper for the institutional symbol of the
     retail slug -- `ctx["live_book_identity"]` or `IDENTITY_MAPPER` here,
     a callable mapper(slug, order_intent) -> {"status": "EXACT",
     "symbol": <institutional symbol>, ...}. No mapper (the default), or any
     answer other than EXACT with a symbol, records NOT_ESTABLISHED with
     reason IDENTITY_MAPPING_NOT_ESTABLISHED and reads NO stream;
  2. reads the resident stream book through `institutional_stream.current`
     (its public API; `ctx["live_book_reader"]` or `READER` override it);
  3. evaluates P5_LIVE_STREAM_BOOK_V1 (`live_book_currency.evaluate`) with
     the decision's actual price source -- the REST paper book -- so C12
     records that the executable price did not come from the certified
     stream observation (verdict NOT_ESTABLISHED, PRICED_BOOK_IS_NOT_THE_
     EVALUATED_STREAM_BOOK) while `stream_book_verdict` shows what the stream
     book alone would be.

Returns the compact admission record (`live_book_currency.admission_record`)
plus `stream_read` (True only when a stream book was evaluated). NEVER RAISES.
Changes no paper behaviour: the paper decision keeps its BOOK_CURRENCY label
and REST price source.

C12 -- THE ACTUAL LANE PRICED FROM THE EVALUATED STREAM OBSERVATION.
`observe` (installed as `decision_hooks.LIVE_BOOK_STREAM`) makes the ONE
identity answer and the ONE resident stream read for a decision, and turns a
current read into an `observation` (id, receipt instant, epoch, and its book
in the retail level shape, priced by the instrument's own scales). The
decision path prices the ACTUAL lane's facts (book, IOC limit, depth, fees,
EV) from that observation with the paper lane's own sizing and economics,
and evaluates P5 on THE SAME read with `priced_from` = that observation
(`priced_from_stream`), so C12 holds only when price and currency are one
observation. Without a current stream book nothing changes: the price
source stays REST_PAPER_BOOK and C12 refuses as before.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import live_book_currency as LBC

STREAM_SOURCE = "INSTITUTIONAL_STREAM"

#: The integrator installs the institutional slug<->symbol mapper here.
IDENTITY_MAPPER = None
#: The stream read; None -> institutional_stream.current (imported lazily).
READER = None


def _reader(ctx):
    r = (ctx or {}).get("live_book_reader") or READER
    if r is None:
        from . import institutional_stream as IS
        r = IS.current
    return r


def _exact(ident) -> bool:
    return (isinstance(ident, dict)
            and str(ident.get("status") or "").upper() == "EXACT"
            and bool(ident.get("symbol")))


def _dec_text(raw, scale):
    try:
        return format((Decimal(int(raw)) / Decimal(int(scale))).normalize(),
                      "f")
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return None


def stream_observation(read, *, symbol, now) -> dict | None:
    """A CURRENT resident stream read for `symbol` -> the observation the
    actual lane is priced from, or None (not ok, no book, another symbol,
    unscaled). The book is rendered in the retail level shape
    ({"px": {"value"}, "qty"}) by the instrument's OWN scales, exactly."""
    if not (isinstance(read, dict) and read.get("ok") is True
            and isinstance(read.get("book"), dict)):
        return None
    if str(read.get("symbol") or "") != str(symbol or ""):
        return None
    ev = read.get("evidence") if isinstance(read.get("evidence"), dict) else {}
    conn = ev.get("connection") or {}
    snap = ev.get("snapshot") or {}
    mkt = ev.get("market") or {}
    ps, qs = mkt.get("price_scale"), mkt.get("qty_scale")
    recv = snap.get("received_at")
    if not (ps and qs) or recv is None:
        return None
    md = {"source": STREAM_SOURCE, "state": mkt.get("state")}
    for side in ("bids", "offers"):
        lv = []
        for e in read["book"].get(side) or ():
            px, q = _dec_text(e.get("px"), ps), _dec_text(e.get("qty"), qs)
            if px is None or q is None:
                return None
            lv.append({"px": {"value": px, "currency": "USD"}, "qty": q})
        md[side] = lv
    return {"source": STREAM_SOURCE,
            "obs_id": "stream:%s:%s:%.6f" % (conn.get("id"), conn.get("seq"),
                                              float(recv)),
            "observed_at": float(recv),
            "observed_at_is": "OUR_RECEIPT_INSTANT",
            "age_s": round(float(now) - float(recv), 3),
            "connection_epoch": conn.get("seq"),
            "connection_id": conn.get("id"),
            "venue_ts": snap.get("venue_ts"), "symbol": str(symbol),
            "market_state": mkt.get("state"),
            "price_scale": ps, "qty_scale": qs, "market_data": md}


def priced_from_stream(observation: dict) -> dict:
    """C12's `priced_from` for a decision priced from `observation`."""
    o = observation or {}
    return {"source": "STREAM", "connection_epoch": o.get("connection_epoch"),
            "received_at": o.get("observed_at"), "obs_id": o.get("obs_id")}


def observe(ctx, cand, *, now) -> dict:
    """decision_hooks.LIVE_BOOK_STREAM: the decision's ONE identity answer
    and ONE resident stream read. {"identity", "read", "observation",
    "reason"}; `observation` only for a current book of the exactly mapped
    symbol. Asks the stream for the symbol when it runs here. NEVER RAISES;
    reads no REST and sends nothing."""
    slug = (cand or {}).get("us_market_slug")
    out = {"identity": None, "read": None, "observation": None,
           "reason": LBC.R_IDENTITY_UNMAPPED, "us_market_slug": slug}
    try:
        mapper = (ctx or {}).get("live_book_identity") or IDENTITY_MAPPER
        if mapper is None:
            return out
        ident = mapper(slug, (cand or {}).get("side"))
        if not _exact(ident):
            out["identity"] = ident if isinstance(ident, dict) else None
            return out
        out["identity"] = ident
        sym = ident["symbol"]
        custom = (ctx or {}).get("live_book_reader") or READER
        if custom is None:
            from . import institutional_stream as IS
            if IS.BOOKS.state in IS.RUNNING:
                IS.want([sym])
        read = _reader(ctx)(sym, now=now)
        out["read"] = read if isinstance(read, dict) else None
        # An exact mapping is ONE key on both venues (cftc id = symbol =
        # slug, institutional_contract_map): a mapping to any other symbol is
        # never priced from, whatever the mapper said.
        out["observation"] = (stream_observation(read, symbol=sym, now=now)
                              if sym == slug else None)
        if sym != slug:
            out["reason"] = "MAPPED_SYMBOL_IS_NOT_THE_RETAIL_SLUG"
            return out
        out["reason"] = (None if out["observation"] is not None else
                         (read or {}).get("refusal") or "NO_CURRENT_STREAM_BOOK")
        return out
    except Exception as exc:                                    # noqa: BLE001
        out["reason"] = "LIVE_BOOK_OBSERVE_FAILED:%s" % type(exc).__name__
        return out


def evaluate_for(ctx: dict, cand: dict, *, obs, now: float,
                 priced_from: dict | None = None,
                 observed: dict | None = None) -> dict:
    """The full rule output (every component). Never raises. `observed`
    (from `observe`) evaluates THAT read instead of reading again;
    `priced_from` names the decision's actual price source (default: the
    REST paper book, as before)."""
    slug = (cand or {}).get("us_market_slug")
    try:
        if observed is not None:
            ident = observed.get("identity")
            if not _exact(ident):
                return dict(LBC.not_evaluated(
                    reason=LBC.R_IDENTITY_UNMAPPED, us_market_slug=slug,
                    now=now, identity=ident if isinstance(ident, dict)
                    else None), stream_read=False)
            read = observed.get("read")
        else:
            mapper = (ctx or {}).get("live_book_identity") or IDENTITY_MAPPER
            if mapper is None:
                return dict(LBC.not_evaluated(reason=LBC.R_IDENTITY_UNMAPPED,
                                              us_market_slug=slug, now=now),
                            stream_read=False)
            ident = mapper(slug, (cand or {}).get("side"))
            if not (isinstance(ident, dict)
                    and str(ident.get("status") or "").upper() == "EXACT"
                    and ident.get("symbol")):
                return dict(LBC.not_evaluated(
                    reason=LBC.R_IDENTITY_UNMAPPED, us_market_slug=slug,
                    now=now,
                    identity=ident if isinstance(ident, dict) else None),
                    stream_read=False)
            read = _reader(ctx)(ident["symbol"], now=now)
        priced = priced_from if isinstance(priced_from, dict) else {
            "source": "REST_PAPER_BOOK",
            "obs_id": None if obs is None else obs.get("obs_id"),
            "observed_at": None if obs is None else obs.get("observed_at")}
        v = LBC.evaluate(stream_read=read, identity=ident, now=now,
                         us_market_slug=slug, priced_from=priced)
        return dict(v, stream_read=isinstance(read, dict))
    except Exception as exc:                                    # noqa: BLE001
        return dict(LBC.not_evaluated(
            reason="LIVE_BOOK_EVALUATION_FAILED:%s" % type(exc).__name__,
            us_market_slug=slug, now=now), stream_read=False)


def evaluate_decision(ctx: dict, cand: dict, *, now: float, obs=None) -> dict:
    """What the decision path evaluates for its ACTUAL lane: one observe;
    priced from the stream observation when there is one, else from the
    REST paper book. Used by the P5 runtime evaluation."""
    so = observe(ctx, cand, now=now)
    if not _exact(so.get("identity")):
        return evaluate_for(ctx, cand, obs=obs, now=now)
    pf = (priced_from_stream(so["observation"])
          if so.get("observation") else None)
    return evaluate_for(ctx, cand, obs=obs, now=now, priced_from=pf,
                        observed=so)


def for_decision(ctx: dict, cand: dict, *, obs, now: float,
                 priced_from: dict | None = None,
                 observed: dict | None = None) -> dict:
    """decision_hooks.LIVE_BOOK_EVIDENCE: the compact admission record."""
    v = evaluate_for(ctx, cand, obs=obs, now=now, priced_from=priced_from,
                     observed=observed)
    return dict(LBC.admission_record(v), stream_read=v.get("stream_read") is True)
