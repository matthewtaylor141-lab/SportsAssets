"""portfolio_truth parses the venue's REAL field dialect — pinned from
raw rows captured live 2026-08-10 (position_fp / count_fp /
market_exposure_dollars), after the first read parsed a funded account
to zero and nearly re-opened the phantom-fills debate."""

import json

from edge.venues.kalshi import KalshiAdapter


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Sess:
    """Answers the three portfolio endpoints with live-shaped rows."""

    def __init__(self, today):
        self.today = today

    def get(self, url, params=None, headers=None, timeout=None):
        if "positions" in url:
            return _Resp({"market_positions": [
                {"ticker": "KXATPCHALLENGERMATCH-26AUG10MRVBAS-BAS",
                 "position_fp": "526.00",
                 "market_exposure_dollars": "199.880000",
                 "fees_paid_dollars": "4.337400"},
                {"ticker": "KXWNBAGAME-26AUG10TORATL-ATL",
                 "position_fp": "0.00",
                 "market_exposure_dollars": "0.000000"},
            ]})
        if "fills" in url:
            return _Resp({"fills": [
                {"action": "buy", "count_fp": "263.00",
                 "yes_price": 38.0, "is_taker": True,
                 "created_time": f"{self.today}T18:15:01.096925Z"},
                {"action": "buy", "count_fp": "104.00",
                 "yes_price": 48.0, "is_taker": True,
                 "created_time": "2026-01-01T00:00:00Z"},  # not today
            ]})
        return _Resp({"orders": [{"order_id": "o1"}]})


def test_portfolio_truth_reads_the_live_dialect():
    import time

    ad = KalshiAdapter.__new__(KalshiAdapter)
    ad._sess = _Sess(time.strftime("%Y-%m-%d", time.gmtime()))
    ad._auth_headers = lambda *a, **k: {}

    out = ad.portfolio_truth()
    assert out["positions"] == 1, "zero-contract rows are not open"
    assert out["position_contracts"] == 526.0
    assert out["exposure_usd"] == 199.88
    assert out["fills_today"] == 1
    assert out["fills_today_contracts"] == 263.0
    assert out["fills_today_usd"] == round(263 * 0.38, 2)
    assert out["resting_orders"] == 1
    assert "positions_raw_sample" not in out, \
        "a cleanly parsed payload ships numbers, not raw rows"
