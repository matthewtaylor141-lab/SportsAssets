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
"""
from __future__ import annotations

from . import live_book_currency as LBC

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


def evaluate_for(ctx: dict, cand: dict, *, obs, now: float) -> dict:
    """The full rule output (every component). Never raises."""
    slug = (cand or {}).get("us_market_slug")
    try:
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
                reason=LBC.R_IDENTITY_UNMAPPED, us_market_slug=slug, now=now,
                identity=ident if isinstance(ident, dict) else None),
                stream_read=False)
        read = _reader(ctx)(ident["symbol"], now=now)
        priced = {"source": "REST_PAPER_BOOK",
                  "obs_id": None if obs is None else obs.get("obs_id"),
                  "observed_at": None if obs is None
                  else obs.get("observed_at")}
        v = LBC.evaluate(stream_read=read, identity=ident, now=now,
                         us_market_slug=slug, priced_from=priced)
        return dict(v, stream_read=isinstance(read, dict))
    except Exception as exc:                                    # noqa: BLE001
        return dict(LBC.not_evaluated(
            reason="LIVE_BOOK_EVALUATION_FAILED:%s" % type(exc).__name__,
            us_market_slug=slug, now=now), stream_read=False)


def for_decision(ctx: dict, cand: dict, *, obs, now: float) -> dict:
    """decision_hooks.LIVE_BOOK_EVIDENCE: the compact admission record."""
    v = evaluate_for(ctx, cand, obs=obs, now=now)
    return dict(LBC.admission_record(v), stream_read=v.get("stream_read") is True)
