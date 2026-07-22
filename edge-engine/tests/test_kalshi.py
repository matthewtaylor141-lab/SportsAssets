from edge.venues.kalshi import KalshiAdapter


def test_taker_fee_formula():
    a = KalshiAdapter()
    assert abs(a.taker_fee(0.5) - 0.0175) < 1e-9
    assert a.taker_fee(0.01) < a.taker_fee(0.5)


def test_orderbook_conversion(monkeypatch):
    a = KalshiAdapter()

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            # YES bids at 46/45 cents; NO bids at 52/50 cents.
            return {"orderbook": {"yes": [[45, 100], [46, 200]],
                                  "no": [[50, 300], [52, 150]]}}

    monkeypatch.setattr(a._sess, "get", lambda *args, **kw: FakeResp())
    book = a.get_book("EV", "TICKER")
    assert book is not None
    # Best executable YES ask = 100 - best NO bid (52) = 48c.
    assert abs(book.asks[0].price - 0.48) < 1e-9
    assert abs(book.bids[0].price - 0.46) < 1e-9


def test_place_refused_in_shadow():
    import asyncio

    import pytest

    with pytest.raises(RuntimeError):
        asyncio.get_event_loop().run_until_complete(KalshiAdapter().place(None))
