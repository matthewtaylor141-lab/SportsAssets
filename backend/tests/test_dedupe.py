"""Cross-path dedupe: Path A and Path B must derive identical keys for one fill."""

from sportsassets.ingestion.dedupe import make_dedupe_key

TX = "0xAbCdEf0000000000000000000000000000000000000000000000000000000001"


def test_same_fill_same_key_across_formats():
    # Path A computes floats from wei; Path B parses API strings.
    a = make_dedupe_key(TX, "123456789", "BUY", 137704.918033, 0.61, 1_760_000_000)
    b = make_dedupe_key(TX.lower(), "123456789", "buy", "137704.918033", "0.610000", 1_760_000_000)
    assert a == b


def test_numeric_normalization():
    assert make_dedupe_key(TX, "1", "SELL", 84000, 0.5, 1) == make_dedupe_key(
        TX, "1", "SELL", "84000.0000004", "0.4999999", 1
    )


def test_distinct_fills_distinct_keys():
    base = dict(tx_hash=TX, asset="1", side="BUY", size=100, price=0.5, ts_epoch=1000)
    key = make_dedupe_key(**base)
    assert make_dedupe_key(**{**base, "side": "SELL"}) != key
    assert make_dedupe_key(**{**base, "size": 100.000002}) != key
    assert make_dedupe_key(**{**base, "price": 0.500001}) != key
    assert make_dedupe_key(**{**base, "ts_epoch": 1001}) != key
    assert make_dedupe_key(**{**base, "asset": "2"}) != key
    assert make_dedupe_key(**{**base, "tx_hash": TX[:-1] + "2"}) != key
