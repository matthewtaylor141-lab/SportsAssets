"""Kalshi discovery must prove what bet a market is before listing it.

The 2026-08-04 pricing audit found Kalshi discovery ran on canonical_outcome
alone — no line gate, no segment tag. Two live hazards:

* a total whose subtitle dropped the number ("Over") matches ANY sharp rung
  downstream — pair matching lets point-less sides through and the lowest
  alternate rung wins, so the "edge" is the gap between rungs;
* a spread-series outcome that parses as a plain team name gets priced
  against the MONEYLINE fair value — a different bet wearing the team's name.

Polymarket US closed both via bet_identity at discovery; Kalshi has no slug
grammar, so its gate reads the parsed outcome + series ticker instead.
"""

from edge.venues.kalshi import KalshiAdapter


class _Resp:
    status_code = 200

    def __init__(self, events):
        self._events = events

    def json(self):
        return {"events": self._events, "cursor": ""}


class _Sess:
    def __init__(self, events):
        self._events = events

    def get(self, url, params=None, timeout=None):
        return _Resp(self._events)


def _discover(events, series):
    a = KalshiAdapter()
    a._sess = _Sess(events)
    out = []
    a.last_census = {}
    a._discover_series(out, "nfl", series)
    return a, out


def _event(title, markets):
    return {"event_ticker": "EVT", "title": title,
            "markets": [{"ticker": t, "yes_sub_title": sub, "title": mt}
                        for t, sub, mt in markets]}


def test_moneyline_outcomes_pass_through():
    _, out = _discover([_event("Eagles vs Cowboys", [
        ("T1", "Eagles", "Eagles vs Cowboys"),
        ("T2", "Cowboys", "Eagles vs Cowboys"),
    ])], "KXNFLGAME")
    assert len(out) == 1
    assert set(out[0].outcome_tokens.values()) == {"T1", "T2"}


def test_a_total_without_its_number_is_refused():
    a, out = _discover([_event("Eagles vs Cowboys: Total Points", [
        ("T1", "Over", "Eagles vs Cowboys: Total Points"),
        ("T2", "Under", "Eagles vs Cowboys: Total Points"),
    ])], "KXNFLGAME")
    assert out == []
    assert a.last_census.get("bare_total") == 2


def test_a_spread_series_outcome_missing_its_line_is_refused():
    a, out = _discover([_event("Eagles vs Cowboys Spread", [
        ("T1", "Eagles", "Eagles vs Cowboys Spread"),
        ("T2", "Cowboys -7.5", "Eagles vs Cowboys Spread"),
    ])], "KXNFLSPREAD")
    # The lined side alone is < 2 outcomes, so nothing lists — better no
    # market than one priced against the wrong fair value.
    assert out == []
    assert a.last_census.get("untagged_spread") == 1


def test_lined_spreads_and_totals_keep_their_lines():
    _, out = _discover([_event("Eagles vs Cowboys Spread", [
        ("T1", "Eagles -7.5", "Eagles vs Cowboys Spread"),
        ("T2", "Cowboys +7.5", "Eagles vs Cowboys Spread"),
    ])], "KXNFLSPREAD")
    assert len(out) == 1
    keys = set(out[0].outcome_tokens)
    assert any("-7.5" in k for k in keys)


def test_segment_titles_are_tagged_so_halves_never_meet_full_game():
    _, out = _discover([_event("Eagles vs Cowboys 1st Half", [
        ("T1", "Eagles", "Eagles vs Cowboys 1st Half"),
        ("T2", "Cowboys", "Eagles vs Cowboys 1st Half"),
    ])], "KXNFLGAME")
    assert len(out) == 1
    assert all(k.startswith("[h1] ") for k in out[0].outcome_tokens)


# ── PEM normalization: env-var pastes must not brick auth ────────────────

def _fresh_rsa_pem() -> str:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                             NoEncryption()).decode()


def test_pem_loads_in_every_paste_format(monkeypatch):
    pem = _fresh_rsa_pem()
    adapter = KalshiAdapter()
    for variant in (
        pem,                                   # proper multi-line
        pem.replace("\n", "\\n"),              # escaped newlines
        pem.replace("\n", " ").strip(),        # newlines -> spaces
        pem.replace("\n", ""),                 # newlines stripped
        f'"{pem}"',                            # quoted paste
    ):
        monkeypatch.setenv("EDGE_KALSHI_PRIVATE_KEY", variant)
        assert adapter._private_key() is not None


# ── orderbook_fp: the format the venue ACTUALLY serves (probe 18:44Z) ────

def test_fp_orderbook_parses_to_executable_asks():
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"orderbook_fp": {
                "no_dollars": [["0.0100", "28945.00"], ["0.3800", "450.50"],
                               ["0.4200", "120.00"]],
                "yes_dollars": [["0.0100", "99999.00"], ["0.5500", "310.00"]],
            }}

    class _Sess:
        @staticmethod
        def get(url, timeout=None):
            return _Resp()

    a = KalshiAdapter()
    a._sess = _Sess()
    book = a.get_book("EVT", "TICK")
    # Best YES ask = 1 - best (highest) NO bid: 1 - 0.42 = 0.58.
    assert book.asks[0].price == 0.58
    assert book.asks[0].size == 120.00
    # Best YES bid is the highest yes level.
    assert book.bids[0].price == 0.55
    assert not a.book_quiet, "a deep book must not count as quiet"


def test_legacy_cents_orderbook_still_parses():
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"orderbook": {"no": [[40, 100]], "yes": [[55, 50]]}}

    class _Sess:
        @staticmethod
        def get(url, timeout=None):
            return _Resp()

    a = KalshiAdapter()
    a._sess = _Sess()
    book = a.get_book("EVT", "TICK")
    assert book.asks[0].price == 0.6
    assert book.bids[0].price == 0.55


def test_truly_empty_book_counts_quiet_not_error():
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"orderbook_fp": {"no_dollars": [], "yes_dollars": []}}

    class _Sess:
        @staticmethod
        def get(url, timeout=None):
            return _Resp()

    a = KalshiAdapter()
    a._sess = _Sess()
    a.get_book("EVT", "TICK")
    assert a.book_quiet.get("empty_no_side") == 1
    assert not a.book_errors, "quiet is never a venue error"


# --- V2 order endpoint (legacy /portfolio/orders answers HTTP 410) ---

class _OrderResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _OrderSess:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return self.resp


def _order_adapter(resp):
    a = KalshiAdapter()
    a._sess = _OrderSess(resp)
    a._auth_headers = lambda method, path: {}
    return a


def test_v2_taker_order_body_is_the_documented_shape():
    a = _order_adapter(_OrderResp(201, {"order": {
        "order_id": "oid-1", "status": "executed",
        "filled_count": "4.00"}}))
    r = a.place_order("KXMLBGAME-26AUG04BALTEX-BAL", 0.55, 4,
                      client_order_id="c-1", taker=True)
    call = a._sess.calls[0]
    assert call["url"].endswith("/portfolio/events/orders")
    body = call["json"]
    assert body["side"] == "bid"
    assert body["count"] == "4.00" and body["price"] == "0.5500"
    assert body["time_in_force"] == "immediate_or_cancel"
    assert "expiration_time" not in body, \
        "IOC cannot carry expiration_time per the V2 spec"
    assert body["self_trade_prevention_type"] == "taker_at_cross"
    assert r["ok"] and r["order_id"] == "oid-1" and r["count"] == 4


def test_v2_maker_order_rests_with_expiration():
    a = _order_adapter(_OrderResp(201, {"order": {
        "order_id": "oid-2", "status": "resting"}}))
    r = a.place_order("TICK", 0.42, 7, client_order_id="c-2", taker=False)
    body = a._sess.calls[0]["json"]
    assert body["time_in_force"] == "good_till_canceled"
    assert isinstance(body["expiration_time"], int)
    assert r["ok"] and r["status"] == "resting"


def test_v2_partial_fill_count_comes_from_the_response():
    a = _order_adapter(_OrderResp(201, {"order": {
        "order_id": "oid-3", "status": "canceled",
        "filled_count": "1.00"}}))
    r = a.place_order("TICK", 0.30, 6, client_order_id="c-3", taker=True)
    assert r["ok"] and r["count"] == 1, \
        "an IOC that filled 1 of 6 must not book 6"


def test_v2_rejection_surfaces_raw_error():
    a = _order_adapter(_OrderResp(400, {"error": {"code": "bad"}}))
    r = a.place_order("TICK", 0.30, 2, client_order_id="c-4", taker=True)
    assert not r["ok"] and r["status"] == "http_400"
    assert "error" in r["raw"]


# --- maker-first entry pricing: threshold judged at the price we pay ---

def _book(ask, bid=None):
    import time as _t

    from edge.venues.base import BookLevel, MarketBook
    return MarketBook(venue="kalshi", market_id="EVT", outcome_id="T",
                      bids=[BookLevel(bid, 10)] if bid else [],
                      asks=[BookLevel(ask, 10)] if ask else [], ts=_t.time())


def test_plan_entry_rests_one_tick_under_the_ask_fee_free():
    a = KalshiAdapter()
    px, taker = a.plan_entry(_book(0.50, 0.44))
    assert (px, taker) == (0.49, False), \
        "maker post: the threshold must be judged fee-free at 49c"


def test_plan_entry_never_prices_below_the_bid():
    a = KalshiAdapter()
    px, taker = a.plan_entry(_book(0.50, 0.49))
    assert (px, taker) == (0.49, False)


def test_plan_entry_crosses_a_one_tick_market_as_taker():
    a = KalshiAdapter()
    px, taker = a.plan_entry(_book(0.01))
    assert taker is True, "no room to rest: cross and pay the real fee"
