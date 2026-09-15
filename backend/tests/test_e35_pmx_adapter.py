"""E35 (2026-09-10, lane E35-A): the institutional PRE-PRODUCTION venue
adapter (sportsassets/pmx.py) and the mirror worker's venue binding.

Pure unit tests on a FAKE HTTP session: nothing here touches the
network, the key is generated per run, and every pin is a shape the
mirror worker reads (inst/VENUE_CONTRACT.md) or a fact the design
verified from the runner (inst/E35_DESIGN.md): the token flow the
venue's own helper uses, the three scalings (100/0.01, 1000/0.005,
1000/0.001), fractionalQtyScale 100, the preview echoing orderQty
VERBATIM, the four intents on the one long-contract instrument, the
refusal names, the fail-closed buckets, and the binding's default --
MIRROR_VENUE unset is `sportsassets.pmus`, byte for byte.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import subprocess
import sys
import threading

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from sportsassets import pmus, pmx, venue_pace
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms

SLUG = "aec-cs2-all-mgc-2026-09-10"
ACCOUNT = "firms/x-clearing-member/accounts/x-account"
PARTICIPANT = "firms/x-participant/users/x-user"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                         serialization.NoEncryption()).decode()
PUB = _KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                     serialization.PublicFormat.SubjectPublicKeyInfo)
TOKEN = "tok-e35-secret-bearer"


# ------------------------------------------------------------- the fake wire

class _Resp:
    def __init__(self, status: int, body=None, text: str | None = None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.headers: dict = {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Session:
    """routes: [(method, url fragment, response | callable(call) -> response)]."""

    def __init__(self, routes=None):
        self.routes = list(routes or [])
        self.calls: list[dict] = []
        # the default token answer LAST: a test's own token route wins
        self.routes.append(("POST", "/oauth/token",
                            _Resp(200, {"access_token": TOKEN, "token_type": "Bearer",
                                        "expires_in": 180})))

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=None):
        call = {"method": method, "url": url, "headers": dict(headers or {}), "params": params,
                "json": json, "data": data, "timeout": timeout}
        self.calls.append(call)
        for m, frag, h in self.routes:
            if m == method and frag in url:
                return h(call) if callable(h) else h
        return _Resp(404, {"code": 5, "message": f"instrument x: instrument does not exist ({url})"})

    def sent(self, frag: str) -> list[dict]:
        return [c for c in self.calls if frag in c["url"]]


def _inst(symbol=SLUG, ps="100", tick=0.01, fq="100", state="INSTRUMENT_STATE_OPEN"):
    return {"symbol": symbol, "priceScale": ps, "tickSize": tick, "fractionalQtyScale": fq,
            "state": state, "description": "CS2: MGC vs ALL", "minimumTradeQty": "1"}


def _order(oid="o1", side="SIDE_BUY", qty="300", price="55", cum="0", leaves="300",
           state="ORDER_STATE_PENDING_NEW", symbol=SLUG, **extra):
    o = {"id": oid, "symbol": symbol, "side": side, "orderQty": qty, "price": price,
         "cumQty": cum, "avgPx": "0", "leavesQty": leaves, "state": state,
         "timeInForce": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
         "createTime": "2026-09-10T14:00:00Z", "fractionalQuantityScale": "100",
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATED"}
    o.update(extra)
    return o


def _routes(inst_rows=None, open_rows=None, preview=None, insert=None, report=None, bbo=None,
            positions=None):
    rows = [_inst()] if inst_rows is None else inst_rows
    r = [("POST", "/v1/refdata/instruments", _Resp(200, {"instruments": rows, "eof": True}))]
    r.append(("GET", "/v1/trading/orders/open",
              _Resp(200, {"orders": [] if open_rows is None else open_rows})))
    if preview is not None:
        r.append(("POST", "/v1/trading/orders/preview", preview))
    else:
        # the verified echo (14:15Z): the request VERBATIM plus state
        # PENDING_NEW and the instrument's priceScale / fractionalQuantityScale
        ps = str(rows[0]["priceScale"]) if rows else "100"

        def _echo(call):
            req = call["json"]["request"]
            return _Resp(200, {"previewOrder": {**req, "id": "", "state": "ORDER_STATE_PENDING_NEW",
                                                "priceScale": ps, "fractionalQuantityScale": "100",
                                                "cumQty": "0", "leavesQty": req["orderQty"]}})
        r.append(("POST", "/v1/trading/orders/preview", _echo))
    if insert is not None:
        r.append(("POST", "/v1/trading/orders/cancel", _Resp(200, {})))
        r.append(("POST", "/v1/trading/orders", insert))
    else:
        r.append(("POST", "/v1/trading/orders/cancel", _Resp(200, {})))
        r.append(("POST", "/v1/trading/orders", _Resp(200, {"orderId": "o1"})))
    # the sandbox's empty search answer (14:2xZ): rows under `order`
    r.append(("POST", "/v1/report/orders/search",
              report if report is not None
              else _Resp(200, {"order": [], "nextPageToken": "", "lastExecutions": []})))
    if bbo is not None:
        r.append(("GET", "/bbo", bbo))
    if positions is not None:
        r.append(("GET", "/v1/positions", positions))
    return r


@pytest.fixture
def world(monkeypatch):
    """Credentials in the environment, a fresh adapter state, the pacer
    replaced by a counter (no 0.35 s sleeps, and the claims are what
    the test reads), the retail quote read stubbed."""
    for k, v in {"PMX_CLIENT_ID": "cid-1", "PMX_KEY_ID": "kid-1", "PMX_PRIVATE_KEY": PEM,
                 "PMX_PARTICIPANT_ID": PARTICIPANT, "PMX_ACCOUNT": ACCOUNT}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MIRROR_VENUE", raising=False)
    pmx._tok["value"], pmx._tok["minted"] = None, 0.0
    pmx._ref.clear()
    pmx._rep.clear()
    for memo in ("_rep_rows", "_cancelled_at", "_intent_by_clord"):
        # E35 review: the per-id report cache (D8), the cancelled ids
        # (D2), the intent memo (D1); getattr so the pins also run
        # against the pre-review module and show which of them bite
        getattr(pmx, memo, {}).clear()
    pmx._order_symbol.clear()
    pmx._catalogue.clear()
    pmx._open_snap["ts"], pmx._open_snap["rows"] = 0.0, {}
    pmx._pos_cache["ts"], pmx._pos_cache["net"] = 0.0, {}
    for b in pmx.BUCKETS.values():
        b.reset()
    paced: list[float] = []
    monkeypatch.setattr(pmx, "pace", lambda *a, **k: paced.append(1.0) or 0.0)
    penalized: list[float] = []
    monkeypatch.setattr(pmx.venue_pace, "penalize", lambda *a, **k: penalized.append(1.0) or 0.0)
    monkeypatch.setattr(pmus, "bbo_read", lambda client, slug: {"bid": 0.30, "ask": 0.32,
                                                                 "state": "MARKET_STATE_OPEN",
                                                                 "error": None})

    class _Retail:
        class markets:
            @staticmethod
            def retrieve_by_slug(slug):
                return {"market": {"slug": slug, "outcome": "MGC", "title": "CS2: MGC vs ALL"}}

    monkeypatch.setattr(pmus, "_get_client", lambda: _Retail())

    def bind(routes):
        s = _Session(routes)
        pmx._session = s
        return s

    yield {"bind": bind, "paced": paced, "penalized": penalized}
    pmx._session = None


def _buy(**kw):
    args = dict(sell=False, tif=pmx.GTC, intent="ORDER_INTENT_BUY_LONG", post_only=True,
                good_till=None)
    args.update(kw)
    return pmx.submit_fok(SLUG, 0.55, 3, args["sell"], args["tif"], args["intent"],
                          args["post_only"], args["good_till"], paced_pair=True)


# ------------------------------------------------------------- 1. the token

def test_token_is_the_venues_helper_flow_and_refreshes_at_150s(world):
    s = world["bind"](_routes())
    t = pmx._token(now=1000.0)
    assert t == TOKEN
    mint = s.sent("/oauth/token")
    assert len(mint) == 1
    call = mint[0]
    assert call["url"] == "https://pmx-preprod.us.auth0.com/oauth/token"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert call["json"] is None and call["timeout"] == pmx.TIMEOUT
    form = call["data"]
    assert form["client_id"] == "cid-1" and form["grant_type"] == "client_credentials"
    assert form["client_assertion_type"] == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    assert form["audience"] == "https://api.preprod.polymarketexchange.com"
    # the assertion: RS256 under OUR key, kid in the header, the venue's claims
    assertion = form["client_assertion"]
    assert jwt.get_unverified_header(assertion)["kid"] == "kid-1"
    claims = jwt.decode(assertion, PUB, algorithms=["RS256"],
                        audience="https://pmx-preprod.us.auth0.com/")
    assert claims["iss"] == "cid-1" and claims["sub"] == "cid-1"
    assert claims["exp"] - claims["iat"] == 60 and claims["jti"]
    # cached inside the refresh window, re-minted past it
    assert pmx._token(now=1100.0) == TOKEN and len(s.sent("/oauth/token")) == 1
    assert pmx._token(now=1000.0 + pmx.TOKEN_REFRESH_S) == TOKEN
    assert len(s.sent("/oauth/token")) == 2
    assert pmx.TOKEN_REFRESH_S == 150.0
    # the mint is a paced request like any other
    assert len(world["paced"]) == 2


def test_token_mint_is_thread_safe_and_minted_once(world):
    s = world["bind"](_routes())
    out: list[str] = []

    def run():
        out.append(pmx._token(now=5.0))

    ts = [threading.Thread(target=run) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out == [TOKEN] * 8 and len(s.sent("/oauth/token")) == 1


def test_a_failed_mint_names_the_status_and_never_the_key_or_a_token(world, caplog):
    world["bind"]([("POST", "/oauth/token",
                    _Resp(401, {"error": "invalid_client",
                                "error_description": "secret " + TOKEN + " " + PEM[:40]}))])
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(pmx.APIStatusError) as ei:
            pmx._token(now=0.0)
    assert ei.value.status_code == 401 and "invalid_client" in str(ei.value)
    assert TOKEN not in str(ei.value) and "BEGIN" not in str(ei.value)
    assert all("BEGIN" not in r.getMessage() and TOKEN not in r.getMessage() for r in caplog.records)


def test_private_key_accepts_pem_or_base64_and_never_echoes_it(world, monkeypatch):
    import base64
    assert "BEGIN" in pmx._private_key()
    monkeypatch.setenv("PMX_PRIVATE_KEY", base64.b64encode(PEM.encode()).decode())
    assert pmx._private_key() == PEM
    monkeypatch.setenv("PMX_PRIVATE_KEY", "bm90LWEta2V5")   # base64 of 'not-a-key'
    with pytest.raises(RuntimeError) as ei:
        pmx._private_key()
    assert "not-a-key" not in str(ei.value) and "bm90" not in str(ei.value)
    monkeypatch.delenv("PMX_PRIVATE_KEY")
    with pytest.raises(RuntimeError, match="PMX_PRIVATE_KEY unset"):
        pmx._private_key()


def test_every_call_carries_the_five_headers(world):
    s = world["bind"](_routes())
    pmx.open_orders()
    h = s.sent("/v1/trading/orders/open")[0]["headers"]
    assert h["Authorization"] == f"Bearer {TOKEN}"
    assert h["x-participant-id"] == PARTICIPANT and h["X-Client-Id"] == "cid-1"
    assert h["Content-Type"] == "application/json"
    assert len(h["X-Request-Id"]) == 36
    pmx.open_orders()
    ids = [c["headers"]["X-Request-Id"] for c in s.sent("/v1/trading/orders/open")]
    assert len(set(ids)) == 2, "a fresh uuid4 per request"


# ------------------------------------------------------------- 2. scaling

@pytest.mark.parametrize("ps,tick,price,raw", [
    ("100", 0.01, 0.55, 55), ("100", 0.01, 0.555, None), ("100", 0.01, 0.99, 99),
    ("100", 0.01, 1.0, None), ("100", 0.01, 0.0, None),
    ("1000", 0.005, 0.555, 555), ("1000", 0.005, 0.551, None), ("1000", 0.005, 0.55, 550),
    ("1000", 0.001, 0.551, 551), ("1000", 0.001, 0.5555, None),
])
def test_price_scaling_lands_on_the_tick_or_refuses(ps, tick, price, raw):
    sc = pmx._scale(_inst(ps=ps, tick=tick))
    assert not isinstance(sc, str)
    pscale, tick_raw, fq = sc
    assert pscale == int(ps) and fq == 100
    assert tick_raw == {0.01: 1, 0.005: 5, 0.001: 1}[tick]
    assert pmx._price_raw(price, pscale, tick_raw) == raw


def test_scaling_converts_in_both_directions_from_the_instruments_own_scales():
    """The sandbox never answers the quantity question (it echoed
    orderQty "100" verbatim and dropped the order, 14:2xZ), so the
    conversion is the docs' rule applied from the instrument's
    fractionalQtyScale, pinned both ways."""
    sc = pmx.Scaling.of(_inst(ps="100", tick=0.01, fq="100"))
    assert isinstance(sc, pmx.Scaling) and sc.as_tuple() == (100, 1, 100)
    assert sc.qty_raw(1) == 100 and sc.qty_raw(3) == 300 and sc.contracts("300") == 3.0
    assert sc.contracts("100") == 1.0 and sc.contracts("1") == 0.01 and sc.contracts(None) == 0.0
    assert sc.price_raw(0.40) == 40 and sc.price("40") == 0.40 and sc.price("") == 0.0
    mlb = pmx.Scaling(1000, 5, 100)
    assert mlb.price_raw(0.555) == 555 and mlb.price("555") == 0.555 and mlb.price_raw(0.551) is None
    nfl = pmx.Scaling(1000, 1, 100)
    assert nfl.price_raw(0.551) == 551 and nfl.price("551") == 0.551
    one = pmx.Scaling(100, 1, 1)
    assert one.qty_raw(7) == 7 and one.contracts("7") == 7.0
    with pytest.raises(ValueError):
        pmx.Scaling(100, 1, 0)
    for contracts in (1, 3, 145, 4855):
        for s in (sc, mlb, one):
            assert s.contracts(str(s.qty_raw(contracts))) == float(contracts)
    for px in (0.01, 0.40, 0.55, 0.99):
        assert sc.price(str(sc.price_raw(px))) == px


def test_scale_reads_an_integer_tick_in_raw_units_and_refuses_the_unreadable():
    assert pmx._scale(_inst(ps="1000", tick=5)) == (1000, 5, 100)
    assert pmx._scale(_inst(ps="1000", tick=0.0003)) == "scale_unreadable"
    assert pmx._scale(_inst(ps="", tick=0.01)) == "scale_unreadable"
    assert pmx._scale(_inst(fq="0")) == "scale_unreadable"
    assert pmx._scale(None) == "scale_unreadable"


def test_the_wire_is_scaled_and_a_misaligned_price_is_refused_not_rounded(world):
    s = world["bind"](_routes([_inst(ps="1000", tick=0.005)]))
    out = pmx.submit_fok(SLUG, 0.555, 3, False, pmx.GTC, "ORDER_INTENT_BUY_LONG", True, None,
                         paced_pair=True)
    assert out["ok"] is False and out["order_id"] == "o1" and out["status"] == ""
    assert out["raw"]["read_back"] == "not_found"
    req = s.sent("/v1/trading/orders/preview")[0]["json"]["request"]
    assert out["raw"]["request"] == req
    assert req["price"] == "555" and req["orderQty"] == "300" and req["symbol"] == SLUG
    inserts = [c for c in s.calls if c["method"] == "POST" and c["url"].endswith("/v1/trading/orders")]
    assert len(inserts) == 1 and inserts[0]["json"] == req, "the insert sends the previewed request"
    assert req["timeInForce"] == pmx.GTC and req["participateDontInitiate"] is True
    assert req["type"] == "ORDER_TYPE_LIMIT" and req["account"] == ACCOUNT
    assert req["manualOrderIndicator"] == "MANUAL_ORDER_INDICATOR_AUTOMATED" and len(req["clordId"]) == 36
    n = len(s.calls)
    out = pmx.submit_fok(SLUG, 0.551, 3, False, pmx.GTC, "ORDER_INTENT_BUY_LONG", True, None)
    assert out["status"] == "tick_misaligned" and out["ok"] is False and out["order_id"] is None
    assert out["raw"]["price_scale"] == 1000 and out["raw"]["tick_raw"] == 5
    assert len(s.calls) == n, "nothing sent"


def test_order_qty_is_contracts_times_fractional_scale_and_the_echo_must_agree(world):
    def _verbatim_contracts(call):
        req = call["json"]["request"]
        return _Resp(200, {"previewOrder": {**req, "orderQty": "3"}})
    s = world["bind"](_routes(preview=_verbatim_contracts))
    out = _buy()
    assert out["status"] == "preview_mismatch" and out["order_id"] is None
    assert out["raw"]["venue_order_qty_raw"] == "3" and out["raw"]["order_qty_raw"] == 300
    assert not s.sent("/v1/trading/orders")[1:], "no insert after a mismatch"

    def _other_price(call):
        req = call["json"]["request"]
        return _Resp(200, {"previewOrder": {**req, "price": "56"}})
    world["bind"](_routes(preview=_other_price))
    assert _buy()["status"] == "preview_mismatch"

    def _other_scale(call):
        req = call["json"]["request"]
        return _Resp(200, {"previewOrder": {**req, "fractionalQuantityScale": "1"}})
    world["bind"](_routes(preview=_other_scale))
    assert _buy()["status"] == "preview_mismatch"
    world["bind"](_routes(preview=_Resp(200, {"previewOrder": {"state": "ORDER_STATE_PENDING_NEW"}})))
    assert _buy()["status"] == "preview_unreadable"
    world["bind"](_routes(preview=_Resp(200, {})))
    assert _buy()["status"] == "preview_unreadable"


# ------------------------------------------------------------- 3. the four intents

@pytest.mark.parametrize("sell,intent,net,side", [
    (False, "ORDER_INTENT_BUY_LONG", None, "SIDE_BUY"),
    (False, "ORDER_INTENT_BUY_SHORT", None, "SIDE_SELL"),
    # E35 review (D5): every sell reads the venue's position first, so
    # the two intent-named sells hold the position they exit
    (True, "ORDER_INTENT_BUY_LONG", 300.0, "SIDE_SELL"),     # SELL_LONG
    (True, "ORDER_INTENT_BUY_SHORT", -300.0, "SIDE_BUY"),    # SELL_SHORT, the cover
    (True, None, -300.0, "SIDE_BUY"),                        # the venue's short covered
    (True, None, 300.0, "SIDE_SELL"),
    (False, None, None, "SIDE_BUY"),
])
def test_the_four_intents_on_the_one_long_contract(world, sell, intent, net, side):
    pos = _Resp(200, {"positions": ([{"symbol": SLUG, "netPosition": str(net), "cost": "0",
                                     "expired": False}] if net is not None else [])})
    s = world["bind"](_routes(positions=pos))
    out = pmx.submit_fok(SLUG, 0.55, 3, sell, pmx.GTC, intent, True, None)
    assert out["order_id"] == "o1"
    req = s.sent("/v1/trading/orders/preview")[0]["json"]["request"]
    assert req["side"] == side and req["price"] == "55"
    assert out["raw"]["side"] == side
    expect_intent = {("SIDE_BUY", False): "ORDER_INTENT_BUY_LONG",
                     ("SIDE_SELL", False): "ORDER_INTENT_BUY_SHORT",
                     ("SIDE_SELL", True): "ORDER_INTENT_SELL_LONG",
                     ("SIDE_BUY", True): "ORDER_INTENT_SELL_SHORT"}[(side, sell)]
    assert out["raw"]["intent"] == expect_intent


def test_a_sell_without_an_intent_refuses_when_the_position_is_unreadable(world):
    s = world["bind"](_routes(positions=_Resp(503, {"message": "no children to pick from"})))
    out = pmx.submit_fok(SLUG, 0.55, 3, True, pmx.GTC, None, True, None)
    assert out["status"] == "side_unverifiable" and out["order_id"] is None
    assert not s.sent("/v1/trading/orders/preview")


def test_bad_intent_and_the_gtc_post_only_gates(world):
    s = world["bind"](_routes())
    assert _buy(intent="ORDER_INTENT_SELL_LONG")["status"] == "bad_intent"
    assert _buy(tif="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")["status"] == "ioc_refused"
    assert _buy(tif="TIME_IN_FORCE_FILL_OR_KILL")["status"] == "ioc_refused"
    assert pmx.submit_fok(SLUG, 0.55, 3)["status"] == "ioc_refused", "the retail default tif is FOK"
    assert _buy(good_till="2026-09-11T00:00:00Z")["status"] == "gtd_refused"
    assert _buy(post_only=False)["status"] == "post_only_required"
    assert pmx.submit_fok(SLUG, 0.55, 0, False, pmx.GTC, None, True, None)["status"] == "bad_quantity"
    assert pmx.submit_fok(SLUG, 0.55, 2.5, False, pmx.GTC, None, True, None)["status"] == "bad_quantity"
    assert s.calls == [], "no gate sends anything"
    for r in (_buy(intent="ORDER_INTENT_SELL_LONG"), _buy(post_only=False)):
        assert list(r) == ["ok", "order_id", "status", "fill_price", "filled_shares", "raw"]
        assert r["ok"] is False and r["fill_price"] is None and r["filled_shares"] == 0.0


# ------------------------------------------------------------- 4. the instrument gate

def test_no_instrument_and_instrument_closed(world):
    s = world["bind"](_routes(inst_rows=[]))
    out = _buy()
    assert out["status"] == "no_instrument" and out["raw"]["symbol"] == SLUG
    assert not s.sent("/v1/trading/orders/preview")
    # the absence is remembered: a second ask burns no refdata budget
    _buy()
    assert len(s.sent("/v1/refdata/instruments")) == 1
    world["bind"](_routes(inst_rows=[_inst(state="INSTRUMENT_STATE_CLOSED")]))
    pmx._ref.clear()
    out = _buy()
    assert out["status"] == "instrument_closed" and out["raw"]["state"] == "INSTRUMENT_STATE_CLOSED"


def test_refdata_is_batched_by_symbols_cached_and_a_404_is_none_of_them(world):
    s = world["bind"](_routes())
    got = pmx._instruments([SLUG, "AEC-Other"], pmx._Claims())
    body = s.sent("/v1/refdata/instruments")[0]["json"]
    assert body["symbols"] == [SLUG, "aec-other"] and body["pageSize"] == 50
    assert got[SLUG]["symbol"] == SLUG and got["aec-other"] is None
    assert pmx._instruments([SLUG], pmx._Claims())[SLUG] is got[SLUG]
    assert len(s.sent("/v1/refdata/instruments")) == 1
    pmx._ref.clear()
    world["bind"]([("POST", "/v1/refdata/instruments",
                    _Resp(404, {"code": 5, "message": "instrument does not exist"}))])
    assert pmx._instruments(["nope"], pmx._Claims()) == {"nope": None}


# ------------------------------------------------------------- 5. the venue's refusals

def test_a_4xx_on_the_insert_is_post_only_rejected_with_the_receipt_fields(world):
    s = world["bind"](_routes(insert=_Resp(400, {"code": 3, "message": "post-only would cross"})))
    out = _buy()
    assert out["status"] == "post_only_rejected" and out["ok"] is False and out["order_id"] is None
    raw = out["raw"]
    assert raw["status_code"] == 400 and raw["error"] == "post-only would cross"
    assert raw["error_type"] == "APIStatusError"
    assert raw["body"] == {"code": 3, "message": "post-only would cross"}
    assert raw["preview"]["price"] == "55"
    assert not s.sent("/v1/trading/orders/open"), "nothing rests: no read-back"
    assert world["penalized"] == []


def test_a_5xx_or_a_dropped_connection_on_the_insert_re_raises(world):
    world["bind"](_routes(insert=_Resp(503, {"message": "upstream"})))
    with pytest.raises(pmx.APIStatusError) as ei:
        _buy()
    assert ei.value.status_code == 503

    def _drop(call):
        raise ConnectionError("reset by peer")
    world["bind"](_routes(insert=_drop))
    with pytest.raises(ConnectionError):
        _buy()
    world["bind"](_routes(insert=_Resp(200, {"ok": True})))
    with pytest.raises(pmx.PmxError, match="without an order id"):
        _buy()


def test_the_latency_stopgap_is_latency_reject_and_never_a_back_off(world):
    world["bind"](_routes(insert=_Resp(400, {"code": 8, "message": "Global Rate Limit Exceeded"})))
    out = _buy()
    assert out["status"] == "latency_reject" and out["order_id"] is None
    assert out["raw"]["status_code"] == 400 and out["raw"]["error"] == "Global Rate Limit Exceeded"
    assert world["penalized"] == []
    assert not ms.is_rate_limit(out["raw"]["error"]) and not ml._raw_rate_limit(out["raw"])


def test_a_429_on_the_insert_trips_the_circuit_and_is_read_as_a_rate_limit(world):
    world["bind"](_routes(insert=_Resp(429, {"code": 8, "message": "rate limit exceeded"})))
    out = _buy()
    assert out["status"] == "post_only_rejected"
    assert out["raw"]["status_code"] == 429 and out["raw"]["error_type"] == "RateLimitError"
    assert world["penalized"] == [1.0]
    assert ml._raw_rate_limit(out["raw"])
    # and from the preview it RAISES, as the retail SDK's does
    world["bind"](_routes(preview=_Resp(429, {"code": 8, "message": "rate limit exceeded"})))
    with pytest.raises(pmx.RateLimitError) as ei:
        _buy()
    assert ms.is_rate_limit(ei.value) and ei.value.status_code == 429


def test_a_placed_rest_reads_its_state_back_and_keeps_the_id_when_the_read_fails(world, caplog):
    s = world["bind"](_routes(open_rows=[_order()]))
    with caplog.at_level(logging.INFO, logger=pmx.log.name):
        out = _buy()
    assert out == {"ok": False, "order_id": "o1", "status": "pending_new", "fill_price": None,
                   "filled_shares": 0.0, "raw": out["raw"]}
    assert out["raw"]["executions"] == [] and out["raw"]["open_order"]["state"] == "pending_new"
    assert out["raw"]["response"] == {"orderId": "o1"} and out["raw"]["open_read_error"] is None
    assert out["raw"]["read_back"] == "open"
    rb = s.sent("/v1/trading/orders/open")
    assert len(rb) == 1 and rb[0]["params"] == {"symbols": [SLUG]}
    assert not s.sent("/v1/report/orders/search"), "found on open orders: no report search"
    # the exact body sent is logged once, for the ladder's next probe
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("pmx: insert ")]
    assert len(lines) == 1 and json.loads(lines[0][len("pmx: insert "):]) == out["raw"]["request"]
    assert TOKEN not in lines[0] and "BEGIN" not in lines[0]
    # a taker hit the fresh rest inside the window: the row's fill is the fill
    world["bind"](_routes(open_rows=[_order(cum="100", leaves="200", avgPx="55",
                                            state="ORDER_STATE_PARTIALLY_FILLED")]))
    out = _buy()
    assert out["ok"] is True and out["filled_shares"] == 1.0 and out["fill_price"] == 0.55
    assert out["status"] == "partially_filled"
    # not on open orders but on the report by orderIds: the report's state
    done = _order(oid="o1", cum="300", leaves="0", avgPx="55", state="ORDER_STATE_FILLED")
    pmx._rep.clear()
    pmx._rep_rows.clear()
    s = world["bind"](_routes(report=_Resp(200, {"order": [done], "nextPageToken": "",
                                                  "lastExecutions": []})))
    out = _buy()
    assert out["status"] == "filled" and out["ok"] is True and out["filled_shares"] == 3.0
    assert out["raw"]["read_back"] == "report"
    assert s.sent("/v1/report/orders/search")[0]["json"] == {"orderIds": ["o1"], "pageSize": 100}
    # the quickstart's response shape: {order: {id}}
    world["bind"](_routes(insert=_Resp(200, {"order": {"id": "o9", "state": "ORDER_STATE_NEW"}})))
    out = _buy()
    assert out["order_id"] == "o9" and out["status"] == "" and out["raw"]["read_back"] == "not_found"
    # the read-back raising keeps the id, names the read, status empty
    world["bind"](_routes(open_rows=[_order()]) + [])
    pmx._session.routes = [r for r in pmx._session.routes if "/orders/open" not in r[1]]
    pmx._session.routes.append(("GET", "/v1/trading/orders/open", _Resp(502, {"message": "gw"})))
    out = _buy()
    assert out["order_id"] == "o1" and out["status"] == "" and out["raw"]["read_back"] == "unread"
    assert out["raw"]["open_read_error"].startswith("APIStatusError: gw")


def test_an_acknowledged_order_the_venue_shows_nowhere_is_the_workers_order_state_unknown(world):
    """The sandbox probe of 14:19Z: 200 {"orderId": "CDHM9PJV16R7"} and
    then nothing -- open orders [], positions [], every report search
    empty. The placement keeps the id with an EMPTY status: the
    worker's `_place` reads an empty status as non-terminal (the row
    stays open with its id), the next tick's `_reconcile_open` asks
    order_status, and None there is the `order_state_unknown` freeze.
    The word "unknown" would be read as TERMINAL by `_rest_terminal`
    and close the row on an order that may yet rest."""
    from sportsassets import live_executor as le
    world["bind"](_routes(insert=_Resp(200, {"orderId": "CDHM9PJV16R7"})))
    out = _buy()
    assert out["order_id"] == "CDHM9PJV16R7" and out["status"] == "" and out["ok"] is False
    assert out["raw"]["read_back"] == "not_found"
    status = str(out.get("status") or "")
    assert not le._rest_terminal({"state": status} if status else None)
    assert le._rest_terminal({"state": "unknown"}), "the word the placement must not return"
    assert 'if status else None' in inspect.getsource(ml._place_reserved)
    pmx._open_snap["ts"] = 0.0
    assert pmx.order_status("CDHM9PJV16R7") is None
    body = pmx._session.sent("/v1/report/orders/search")[-1]["json"]
    assert body == {"orderIds": ["CDHM9PJV16R7"], "pageSize": 100}
    assert "order_state_unknown" in inspect.getsource(ml._reconcile_open)


# ------------------------------------------------------------- 6. cancel

def test_cancel_order_never_raises(world):
    s = world["bind"](_routes())
    assert pmx.cancel_order("o1", SLUG) == {"ok": True}
    assert s.sent("/v1/trading/orders/cancel")[0]["json"] == {"orderId": "o1", "symbol": SLUG}
    world["bind"]([("POST", "/v1/trading/orders/cancel",
                    _Resp(409, {"message": "already filled"}))])
    out = pmx.cancel_order("o1", SLUG)
    assert out == {"ok": False, "error": "APIStatusError: already filled"}

    def _drop(call):
        raise ConnectionError("reset")
    world["bind"]([("POST", "/v1/trading/orders/cancel", _drop)])
    assert pmx.cancel_order("o1", SLUG) == {"ok": False, "error": "ConnectionError: reset"}
    world["bind"]([("POST", "/v1/trading/orders/cancel", _Resp(429, {"message": "rate limit exceeded"}))])
    out = pmx.cancel_order("o1", SLUG)
    assert out["ok"] is False and ms.is_rate_limit(out["error"]) and world["penalized"] == [1.0]
    os.environ.pop("PMX_ACCOUNT")
    os.environ.pop("PMX_CLIENT_ID")
    out = pmx.cancel_order("o1", SLUG)
    assert out["ok"] is False and out["error"].startswith("RuntimeError: pmx: PMX_CLIENT_ID unset")


# ------------------------------------------------------------- 7. open orders / status

def test_open_orders_rows_are_the_retail_literal_and_unreadable_raises(world):
    s = world["bind"](_routes(open_rows=[_order(), _order(oid="o2", side="SIDE_SELL", price="60",
                                                          cum="100", leaves="200", avgPx="60",
                                                          state="ORDER_STATE_PARTIALLY_FILLED")]))
    rows = pmx.open_orders([SLUG])
    assert s.sent("/v1/trading/orders/open")[0]["params"] == {"symbols": [SLUG]}
    assert list(rows[0]) == list(pmus._norm_order({"id": "x"})), "the retail row's keys, in order"
    assert rows[0] == {"order_id": "o1", "us_market_slug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
                       "side": "BUY", "venue_side": "ORDER_SIDE_BUY", "price": 0.55, "quantity": 3.0,
                       "filled_shares": 0.0, "leaves": 3.0, "avg_px": None, "state": "pending_new",
                       "title": "MGC vs ALL", "created_at": "2026-09-10T14:00:00Z",
                       "tif": "GOOD_TILL_CANCEL"}
    assert rows[1]["intent"] == "ORDER_INTENT_SELL_LONG" and rows[1]["side"] == "SELL"
    assert rows[1]["venue_side"] == "ORDER_SIDE_SELL" and rows[1]["state"] == "partially_filled"
    assert rows[1]["filled_shares"] == 1.0 and rows[1]["avg_px"] == 0.60 and rows[1]["leaves"] == 2.0
    assert rows[0]["state"] in pmx.OPEN_ORDER_STATES and pmx.OPEN_ORDER_STATES is pmus.OPEN_ORDER_STATES
    assert pmx.open_orders() and s.sent("/v1/trading/orders/open")[-1]["params"] is None
    world["bind"]([("GET", "/v1/trading/orders/open", _Resp(500, {"message": "down"}))])
    with pytest.raises(pmx.APIStatusError):
        pmx.open_orders()
    # an open order on a symbol refdata does not carry cannot be scaled: a raise, never 0.0
    pmx._ref.clear()
    world["bind"](_routes(inst_rows=[], open_rows=[_order()]))
    with pytest.raises(pmx.PmxError, match="scale_unreadable"):
        pmx.open_orders()


def test_order_status_none_vs_row(world):
    s = world["bind"](_routes(open_rows=[_order()]))
    row = pmx.order_status("o1")
    assert row["state"] == "pending_new" and row["order_id"] == "o1"
    assert row["commission_usd"] is None and row["commission_spread_px"] is None
    assert row["executions"] is None
    assert not s.sent("/v1/report/orders/search"), "the snapshot answered"
    assert pmx.order_status("o1") is not None and len(s.sent("/v1/trading/orders/open")) == 1, \
        "the shared snapshot is fresh for 2 s"
    assert pmx.order_status("o-gone") is None
    assert s.sent("/v1/report/orders/search")[0]["json"] == {"orderIds": ["o-gone"], "pageSize": 100}
    done = _order(oid="o7", cum="300", leaves="0", avgPx="55", state="ORDER_STATE_FILLED",
                  commissionNotionalTotalCollected="1250")
    ex = {"id": "e1", "order": {"id": "o7"}, "type": "EXECUTION_TYPE_FILL", "lastShares": "300",
          "lastPx": "55", "tradeId": "t1", "aggressor": False, "transactTime": "2026-09-10T14:05:00Z"}
    other = {"id": "e2", "order": {"id": "o8"}, "type": "EXECUTION_TYPE_FILL"}
    pmx._rep.clear()
    pmx._rep_rows.clear()
    world["bind"](_routes(open_rows=[], report=_Resp(200, {"order": [done], "nextPageToken": "",
                                                            "lastExecutions": [ex, other]})))
    pmx._open_snap["ts"] = 0.0
    row = pmx.order_status("o7")
    assert row["state"] == "filled" and row["filled_shares"] == 3.0 and row["avg_px"] == 0.55
    assert row["commission_usd"] == 1250 / (100 * 100)
    assert [e["id"] for e in row["executions"]] == ["e1"]
    assert row["executions"][0]["type"] == "EXECUTION_TYPE_FILL" and row["executions"][0]["aggressor"] is False
    # E35 review (D8): o1 left the open list, so it rides the search beside o7
    assert pmx._session.sent("/v1/report/orders/search")[0]["json"] == {"orderIds": ["o1", "o7"], "pageSize": 100}
    # the quickstart's `orders` key is read too; a page without a list: executions None
    pmx._rep.clear()
    pmx._rep_rows.clear()
    world["bind"](_routes(open_rows=[], report=_Resp(200, {"orders": [done]})))
    pmx._open_snap["ts"] = 0.0
    row = pmx.order_status("o7")
    assert row["state"] == "filled" and row["executions"] is None
    # the report budget empty with nothing cached: None, nothing sent
    pmx._rep.clear()
    pmx._rep_rows.clear()
    n = len(pmx._session.calls)
    while pmx.BUCKETS["report"].take():
        pass
    assert pmx.order_status("o8") is None and len(pmx._session.calls) == n


# ------------------------------------------------------------- 8. recent trades

def test_recent_trades_rows_from_the_report_orders(world):
    filled = _order(oid="o7", cum="300", leaves="0", avgPx="55", state="ORDER_STATE_FILLED",
                    lastTransactTime="2026-09-10T14:05:00.123456789Z",
                    priceToQuantityFilled={"55": "200", "54": "100"})
    world["bind"](_routes(report=_Resp(200, {"order": [filled, _order()], "nextPageToken": "",
                                              "lastExecutions": []})))
    rows = pmx.recent_trades(SLUG, 0.0)
    assert len(rows) == 2 and [r["qty"] for r in rows] == [2.0, 1.0]
    r = rows[0]
    assert list(r) == ["qty", "price", "side", "ts", "realized_pnl", "order_id", "order_qty",
                       "order_price", "order_tif", "aggressor", "manual", "own_order_id", "own_side",
                       "own_intent", "own_qty", "own_price", "own_tif", "self"]
    assert r["price"] == 0.55 and r["side"] == "ORDER_SIDE_BUY" and r["own_side"] == "BUY"
    assert r["own_intent"] == "BUY_LONG" and r["order_id"] == "o7" == r["own_order_id"]
    assert r["order_qty"] == 3.0 and r["order_price"] == 0.55 and r["order_tif"] == "GOOD_TILL_CANCEL"
    assert r["aggressor"] is None and r["manual"] is False and r["self"] is False
    assert abs(r["ts"] - 1789049100.123456) < 1e-3
    assert pmx.recent_trades(SLUG, r["ts"] + 1) == []
    assert pmx._session.sent("/v1/report/orders/search")[0]["json"] == {"symbols": [SLUG], "pageSize": 100}
    # no levels: one row at avgPx
    pmx._rep.clear()
    pmx._rep_rows.clear()
    world["bind"](_routes(report=_Resp(200, {"order": [{**filled, "priceToQuantityFilled": {}}]})))
    assert [(x["qty"], x["price"]) for x in pmx.recent_trades(SLUG, 0.0)] == [(3.0, 0.55)]


def test_recent_trades_raises_on_truncation_or_an_undated_fill_never_no_fills(world):
    pmx._rep.clear()
    pmx._rep_rows.clear()
    page = _Resp(200, {"order": [], "nextPageToken": "p2"})
    s = world["bind"](_routes(report=page))
    with pytest.raises(RuntimeError, match="trade log truncated after 3 pages before reaching 10"):
        pmx.recent_trades(SLUG, 10.0, max_pages=3)
    bodies = [c["json"] for c in s.sent("/v1/report/orders/search")]
    assert bodies[0] == {"symbols": [SLUG], "pageSize": 100}
    # the fake hands the same token back, so the third page is the second's
    # body and the 10 s cache answers it without a request
    assert bodies[1] == {"symbols": [SLUG], "pageSize": 100, "pageToken": "p2"} and len(bodies) == 2
    pmx._rep.clear()
    pmx._rep_rows.clear()
    undated = _order(oid="o7", cum="300", state="ORDER_STATE_FILLED")
    world["bind"](_routes(report=_Resp(200, {"orders": [undated]})))
    with pytest.raises(pmx.PmxError, match="no lastTransactTime"):
        pmx.recent_trades(SLUG, 0.0)
    pmx._rep.clear()
    pmx._rep_rows.clear()
    world["bind"]([("POST", "/v1/report/orders/search", _Resp(500, {"message": "down"}))])
    with pytest.raises(pmx.APIStatusError):
        pmx.recent_trades(SLUG, 0.0)
    while pmx.BUCKETS["report"].take():
        pass
    with pytest.raises(pmx.RateBudget):
        pmx.recent_trades(SLUG, 0.0)


# ------------------------------------------------------------- 9. the quote read

def test_bbo_read_is_the_retail_book_with_preprods_top_beside_it(world):
    s = world["bind"](_routes(bbo=_Resp(200, {"symbol": SLUG, "bestBid": {"px": "30", "qty": "500"},
                                              "bestOffer": {}, "state": "INSTRUMENT_STATE_OPEN"})))
    out = pmx.bbo_read(pmx._get_client(), SLUG)
    assert list(out)[:4] == ["bid", "ask", "state", "error"]
    assert out["bid"] == 0.30 and out["ask"] == 0.32 and out["state"] == "MARKET_STATE_OPEN"
    assert out["error"] is None
    assert out["pmx_bid"] == 0.30 and out["pmx_ask"] is None
    assert out["pmx_state"] == "INSTRUMENT_STATE_OPEN" and out["pmx_error"] is None
    assert s.sent("/bbo")[0]["url"].endswith(f"/v1/orderbook/{SLUG}/bbo")
    # the 12/min bucket empty: the retail quote stands, preprod's is `unread`, nothing sent
    while pmx.BUCKETS["book"].take():
        pass
    n = len(s.calls)
    out = pmx.bbo_read(None, SLUG)
    assert out["bid"] == 0.30 and out["pmx_bid"] is None and out["pmx_state"] == "unread"
    assert len(s.calls) == n
    # a symbol preprod does not list
    pmx._ref.clear()
    world["bind"](_routes(inst_rows=[]))
    out = pmx.bbo_read(None, "aec-itfme-matesta-leoros-2026-09-10")
    assert out["bid"] == 0.30 and out["pmx_state"] == "no_instrument"
    # a failing preprod read is named beside the retail quote, never raised
    pmx._ref.clear()
    pmx.BUCKETS["book"].reset()
    world["bind"](_routes(bbo=_Resp(404, {"code": 5, "message": "instrument does not exist"})))
    out = pmx.bbo_read(None, SLUG)
    assert out["bid"] == 0.30 and out["pmx_state"] == "unread" and out["pmx_error"] == "APIStatusError"


# ------------------------------------------------------------- 10. the shim

def test_the_client_shim_positions_page_and_catalogue_delegation(world):
    pos = _Resp(200, {"positions": [{"symbol": SLUG, "netPosition": "-300", "cost": "-16500",
                                     "expired": False}],
                      "availablePosition": []})
    s = world["bind"](_routes(positions=pos))
    client = pmx._get_client()
    assert client is pmx._get_client()
    page = client.portfolio.positions({"limit": 100})
    assert s.sent("/v1/positions")[0]["params"] == {"name": ACCOUNT}
    assert page["nextCursor"] is None and page["eof"] is True
    p = page["positions"][SLUG]
    assert p["netPosition"] == -3.0 and p["expired"] is False
    assert p["cost"] == -16500 / (100 * 100)
    assert p["marketMetadata"] == {"outcome": "MGC", "title": "CS2: MGC vs ALL"}
    assert client.markets.retrieve_by_slug(SLUG)["market"]["outcome"] == "MGC"
    # the worker's helpers run through it unchanged
    echo, pages = ml._position_echo(pmx, SLUG)
    assert echo == {"net": -3.0, "outcome": "MGC", "title": "CS2: MGC vs ALL"} and pages == 1
    assert ml._market_read(pmx, SLUG)["outcome"] == "MGC"
    # a position on a symbol refdata does not carry is an unreadable walk, never a missing slug
    pmx._ref.clear()
    world["bind"](_routes(inst_rows=[], positions=pos))
    with pytest.raises(pmx.PmxError):
        pmx._get_client().portfolio.positions({"limit": 100})


def test_balance_and_probe_read_the_venues_own_figures_and_never_order(world):
    bal = _Resp(200, {"balance": "1000000", "excessCapital": "1000000", "buyingPower": "1000000",
                      "openOrders": "0", "updateTime": "2026-09-02T21:30:37Z"})
    s = world["bind"]([("POST", "/v1/positions/balance", bal),
                       ("GET", "/v1/health", _Resp(200, {"status": "ok"})),
                       ("GET", "/v1/whoami", _Resp(200, {"user": "firms/x/users/u", "firm": "firms/x",
                                                         "firmType": "FIRM_TYPE_PARTICIPANT"}))])
    assert pmx.balance()["buyingPower"] == "1000000"
    assert s.sent("/v1/positions/balance")[0]["json"] == {"account": ACCOUNT, "currency": "USD"}
    out = pmx.probe()
    assert out["health_ok"] and out["auth_ok"] and out["creds_configured"]
    assert out["whoami"]["firmType"] == "FIRM_TYPE_PARTICIPANT" and out["balance"]["balance"] == "1000000"
    assert out["venue"] == "pmx_preprod" and out["base_url"] == pmx.PMX_BASE_URL
    assert not any(c["url"].endswith("/v1/trading/orders") for c in s.calls)
    world["bind"]([("POST", "/v1/positions/balance", _Resp(503, {"message": "no children to pick from"}))])
    out = pmx.probe()
    assert out["health_ok"] is False and out["auth_ok"] is False
    assert out["balance_error"].startswith("APIStatusError: no children")


def test_account_positions_walk_runs_through_the_shim(world):
    import asyncio
    pos = _Resp(200, {"positions": [{"symbol": SLUG, "netPosition": "500", "cost": "0"}]})
    world["bind"](_routes(positions=pos))
    positions, pages, limited = asyncio.run(ms.account_positions_walk(pmx))
    assert positions == {SLUG: 5.0} and pages == 1 and limited is False
    world["bind"](_routes(positions=_Resp(429, {"message": "rate limit exceeded"})))
    positions, pages, limited = asyncio.run(ms.account_positions_walk(pmx))
    assert positions is None and limited is True


# ------------------------------------------------------------- 11. the buckets and the claims

def test_the_buckets_fail_closed_and_refill_per_minute():
    b = pmx._Bucket(6)
    assert all(b.take(now=100.0) for _ in range(6)) and not b.take(now=100.0)
    assert not b.take(now=105.0) and b.take(now=110.0) and not b.take(now=110.0)
    assert {k: v.cap for k, v in pmx.BUCKETS.items()} == {"refdata": 6.0, "book": 12.0, "report": 12.0}


def test_refdata_budget_empty_is_the_rate_budget_refusal_or_the_stale_cache(world):
    s = world["bind"](_routes())
    while pmx.BUCKETS["refdata"].take():
        pass
    out = _buy()
    assert out["status"] == "rate_budget" and s.calls == []
    pmx.BUCKETS["refdata"].reset()
    assert _buy()["order_id"] == "o1"
    # stale beyond the TTL with the bucket empty: the cached row answers
    pmx._ref[SLUG] = (pmx._ref[SLUG][0], -1e9)
    while pmx.BUCKETS["refdata"].take():
        pass
    assert _buy()["order_id"] == "o1" and len(s.sent("/v1/refdata/instruments")) == 1


def test_one_pacer_claim_per_http_request_the_callers_covering_the_first(world):
    s = world["bind"](_routes(open_rows=[_order()]))
    paced = world["paced"]
    _buy()
    # token mint, refdata, preview, insert, open read: five requests; the
    # caller (_paced) claimed the gap for the call's first, so four here
    assert len(s.calls) == 5 and len(paced) == 4
    del paced[:]
    del s.calls[:]
    _buy()                              # instrument and token cached: preview, insert, open read
    assert len(s.calls) == 3 and len(paced) == 2
    del paced[:]
    del s.calls[:]
    pmx.open_orders()
    assert len(s.calls) == 1 and len(paced) == 0
    world["bind"](_routes(bbo=_Resp(200, {"bestBid": {"px": "30"}, "state": "INSTRUMENT_STATE_OPEN"})))
    del paced[:]
    pmx.bbo_read(None, SLUG)            # the caller's gap went to the retail read: pmx's claims its own
    assert len(pmx._session.calls) == 1 and len(paced) == 1


# ------------------------------------------------------------- 12. the contract pins

def test_contract_pins():
    assert pmx.submit_fok.__name__ == "submit_fok"
    params = list(inspect.signature(pmx.submit_fok).parameters)
    assert params == ["us_market_slug", "limit_price", "quantity", "sell", "tif", "intent",
                      "post_only", "good_till", "paced_pair"]
    assert params == list(inspect.signature(pmus.submit_fok).parameters)
    assert ml._write_slots(pmx.submit_fok, (SLUG, 0.5, 1, True)) == 1
    assert ml._write_slots(pmx.submit_fok, (SLUG, 0.5, 1, False)) == 2
    assert pmx.resolve_market_exact is pmus.resolve_market_exact
    assert pmx.resolve_derivative_exact is pmus.resolve_derivative_exact
    assert pmx.resolve_team_yesno_exact is pmus.resolve_team_yesno_exact
    assert pmx.order_intent_for is pmus.order_intent_for
    assert pmx._yn_name_match is pmus._yn_name_match
    assert ms._lane_available(pmx)
    for name in ("submit_fok", "cancel_order", "open_orders", "order_status", "recent_trades",
                 "bbo_read", "_get_client", "resolve_market_exact", "resolve_derivative_exact",
                 "resolve_team_yesno_exact", "order_intent_for", "_yn_name_match"):
        assert callable(getattr(pmx, name)), name
    assert pmx.close_position(SLUG, slippage_bips=10) == {
        "ok": False, "status": "not_supported", "slug": SLUG,
        "why": "no close-position endpoint on the institutional venue; the mirror never calls it"}
    assert pmx.PMX_AUTH0_DOMAIN == "pmx-preprod.us.auth0.com"
    assert pmx.PMX_BASE_URL == "https://api.preprod.polymarketexchange.com"
    assert "prod." not in pmx.PMX_BASE_URL.replace("preprod.", "") and pmx.VENUE == "pmx_preprod"
    src = inspect.getsource(pmx)
    assert "api.prod.polymarketexchange.com" not in src and "pmx-prod.us.auth0.com" not in src
    assert "RateLimit" in pmx.RateLimitError.__name__ and ms.is_rate_limit(pmx.RateLimitError(429, "x"))
    assert not ms.is_rate_limit(pmx.RateBudget("x")) and not ms.is_rate_limit(pmx.APIStatusError(400, "429 x"))


def test_the_import_refuses_a_non_preprod_host():
    with pytest.raises(RuntimeError, match="not the preprod host"):
        pmx._host_guard({"PMX_BASE_URL": "https://api.prod.polymarketexchange.com"})
    with pytest.raises(RuntimeError, match="not the preprod host"):
        pmx._host_guard({"PMX_AUTH0_DOMAIN": "pmx-prod.us.auth0.com"})
    with pytest.raises(RuntimeError, match="not the preprod host"):
        pmx._host_guard({"PMX_AUDIENCE": "https://api.prod.polymarketexchange.com/"})
    pmx._host_guard({"PMX_BASE_URL": "https://api.preprod.polymarketexchange.com/",
                     "PMX_AUTH0_DOMAIN": "https://pmx-preprod.us.auth0.com", "PMX_AUDIENCE": ""})
    env = {**os.environ, "PMX_BASE_URL": "https://api.prod.polymarketexchange.com"}
    r = subprocess.run([sys.executable, "-c", "import sportsassets.pmx"], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "not the preprod host" in r.stderr


def test_the_private_key_and_the_token_never_reach_a_log_or_a_refusal(world, caplog):
    world["bind"](_routes(insert=_Resp(400, {"message": "would cross"})))
    with caplog.at_level(logging.DEBUG):
        out = _buy()
    text = json.dumps(out, default=str) + " ".join(r.getMessage() for r in caplog.records)
    assert "BEGIN" not in text and TOKEN not in text and PEM[30:60] not in text


# ------------------------------------------------------------- 13. the binding

def test_the_default_binding_is_the_retail_module_byte_for_byte(monkeypatch):
    monkeypatch.delenv("MIRROR_VENUE", raising=False)
    name, mod = ml._venue_module()
    assert name == "retail" and mod is pmus and mod.__name__ == "sportsassets.pmus"
    monkeypatch.setenv("MIRROR_VENUE", "")
    assert ml._venue_module()[1] is pmus
    monkeypatch.setenv("MIRROR_VENUE", "Retail")
    assert ml._venue_module()[1] is pmus
    monkeypatch.setenv("MIRROR_VENUE", "pmx_preprod")
    name, mod = ml._venue_module()
    assert name == "pmx_preprod" and mod is pmx and mod.__name__ == "sportsassets.pmx"
    monkeypatch.setenv("MIRROR_VENUE", "pmx_prod")
    with pytest.raises(RuntimeError, match="MIRROR_VENUE='pmx_prod' is neither"):
        ml._venue_module()
    src = inspect.getsource(ml.main)
    assert "venue, pmus = _venue_module()" in src and "from .. import pmus" not in src
    assert "_arm_fast(pool, pmus, http)" in src and "await tick_once(pool, pmus, http)" in src
    # the heartbeat's `venue` key rides ONLY under a non-default binding,
    # after the tick's base block; the retail heartbeat is what it was
    assert 'stats["venue"] = venue' in src
    assert src.index("if venue != VENUE_RETAIL:\n                    # E35") < src.index('stats["venue"] = venue')
    assert "venue" not in ml._new_stats()
    assert ml.VENUE_RETAIL == "retail" and ml.VENUE_PMX_PREPROD == "pmx_preprod"
    # nothing else in the worker names the institutional module
    body = inspect.getsource(ml)
    assert body.count("from .. import pmx") == 1 and body.count("from .. import pmus") == 1


def test_the_pmx_module_names_no_hosts_but_preprod_and_carries_the_documented_buckets():
    src = inspect.getsource(pmx)
    assert "6/min" in src and "12/min" in src
    assert pmx.REF_TTL_S == 300.0 and pmx.OPEN_SNAPSHOT_TTL_S == 2.0 and pmx.REPORT_TTL_S == 10.0
    assert pmx.TIMEOUT == (10.0, 30.0)
    assert venue_pace.MIN_GAP_S == 0.35
