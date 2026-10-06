"""THE SAME-BOOK PROBE: STREAM BOOK VS RETAIL BOOK, READ-ONLY, WITHIN 1 s.

The institutional side is the RESIDENT stream book, fed by the production
`GrpcBidiTransport` from grpcio's in-process fake venue (or, for the pure
cases, by `ResidentBooks.on_update` directly). The retail side is a fake
`retail_read` returning the venue's documented `marketData` shape, or the
real keyless SDK client over an httpx MockTransport.

  §1  compare (pure): AGREE_TOP_N, AGREE_TOUCH_ONLY, DISAGREE (the 2026-09-19
      0.97/0.99 vs 0.59/0.60 case), NOT_COMPARABLE; exact Decimal scaling
  §2  one sample against the gRPC-fed stream book: agreement; disagreement;
      the window bound; identity refused; retail unreadable; stream not
      current; the stream book changing inside the window
  §3  the retail read: keyless client, GET /v1/markets/{slug}/book only, any
      other method refused before it is sent, a bad slug never sent
  §4  persisted (pg); orders_placed is CHECKed to zero; the worker helper
      writes one row per symbol and places nothing
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import asyncpg
import httpx
import pytest

from sportsassets import institutional_same_book as SB
from sportsassets import institutional_stream as IS
from sportsassets.workers import institutional_md as WMD

try:
    from tests.test_institutional_contract_map import AEC, SLUG
    from tests.test_institutional_md_grpc_transport import (
        Wait, ack, book, in_thread, transport, until, venue)  # noqa: F401
except ImportError:                                             # pragma: no cover
    from test_institutional_contract_map import AEC, SLUG  # type: ignore
    from test_institutional_md_grpc_transport import (  # type: ignore
        Wait, ack, book, in_thread, transport, until, venue)  # noqa: F401

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    yield
    IS.reset()


def retail_md(bids=(("0.45", "10"),), offers=(("0.47", "5"),)):
    """The retail venue's documented level shape."""
    return {"bids": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                     for p, q in bids],
            "offers": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                       for p, q in offers],
            "state": "MARKET_STATE_OPEN"}


def fake_retail(md=None, ok=True, error=None, calls=None):
    def read(slug):
        if calls is not None:
            calls.append(slug)
        return {"ok": ok, "marketData": md if ok else None, "error": error}
    return read


def books_with(bids=((450, 1000),), offers=((470, 500),), at=None):
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(SLUG, AEC)
    b.want([SLUG])
    b.on_connected("grpc-test")
    b.on_update({"symbol": SLUG, "bids": list(bids), "offers": list(offers),
                 "state": "INSTRUMENT_STATE_OPEN",
                 "transact_time": time.time() if at is None else at})
    return b


# ── §1 compare ──────────────────────────────────────────────────────────

def test_compare_names_each_verdict_exactly():
    s = SB.retail_levels(retail_md())
    assert SB.compare(s, SB.retail_levels(retail_md()))["verdict"] == \
        SB.V_AGREE
    deeper = SB.retail_levels(retail_md(bids=(("0.45", "10"),
                                              ("0.44", "1"))))
    c = SB.compare(s, deeper)
    assert c["verdict"] == SB.V_TOUCH and c["reason"] == \
        "DEEPER_LEVELS_DIFFER"
    size = SB.retail_levels(retail_md(bids=(("0.45", "11"),)))
    c = SB.compare(s, size)
    assert c["verdict"] == SB.V_TOUCH and c["reason"] == "TOUCH_SIZE_DIFFERS"
    # the unresolved 2026-09-19 side-by-side
    inst = SB.retail_levels(retail_md(bids=(("0.97", "1"),),
                                      offers=(("0.99", "1"),)))
    ret = SB.retail_levels(retail_md(bids=(("0.59", "1"),),
                                     offers=(("0.60", "1"),)))
    c = SB.compare(inst, ret)
    assert c["verdict"] == SB.V_DISAGREE and c["reason"] == "BEST_BID_DIFFERS"
    assert c["diff"][0] == {"side": "bids", "level": 0,
                            "stream": ["0.97", "1"], "retail": ["0.59", "1"]}
    assert SB.compare(None, s)["reason"] == SB.NC_STREAM
    assert SB.compare(s, None)["reason"] == SB.NC_RETAIL
    empty = {"bids": [], "offers": []}
    assert SB.compare(empty, empty)["reason"] == SB.NC_EMPTY


def test_scaling_is_exact_and_a_bad_retail_level_is_unreadable():
    b = books_with(bids=((455, 12345),))
    lv = SB.stream_levels(b.current(SLUG))
    from decimal import Decimal
    assert lv["bids"] == [(Decimal("0.455"), Decimal("123.45"))]
    assert SB.retail_levels({"bids": [{"px": {"value": "x"}, "qty": "1"}],
                             "offers": []}) is None
    assert SB.retail_levels("nope") is None
    # retail ladders are ordered best-first whatever order they arrive in
    r = SB.retail_levels(retail_md(bids=(("0.40", "1"), ("0.45", "1"))))
    assert [str(p) for p, _ in r["bids"]] == ["0.45", "0.40"]


# ── §2 one sample, stream fed by the in-process gRPC venue ──────────────

def grpc_books(venue, steps):
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(SLUG, AEC)
    b.want([SLUG])
    gate = Wait()
    venue.scripts = [list(steps) + [gate]]
    t = transport(b, venue)
    th, _ = in_thread(t.run_once)
    until(lambda: b.current(SLUG)["ok"])
    return b, gate, th


def test_agreement_with_the_grpc_fed_stream_book(venue):
    b, gate, th = grpc_books(venue, [ack((SLUG,)), book(symbol=SLUG,
                                                        bids=((450, 1000),),
                                                        offers=((470, 500),))])
    calls = []
    r = SB.sample(SLUG, record=AEC, books_current=b.current,
                  retail_read=fake_retail(retail_md(), calls=calls))
    assert r["verdict"] == SB.V_AGREE and r["identity_ok"] is True
    assert r["matched_stream_read"] == "both"
    assert r["stream_ok"] and r["retail_ok"] and r["within_window"]
    assert r["window_s"] <= SB.MAX_WINDOW_S
    assert r["connection_epoch"] == 1
    assert r["connection_id"].startswith("grpc-")
    assert r["best_bid_equal"] and r["best_offer_equal"]
    assert r["levels_equal"] and r["touch_qty_equal"]
    assert r["stream_book"]["bids"] == [{"price": "0.45", "size": "10"}]
    assert r["orders_placed"] == 0 and calls == [SLUG]
    gate.set()
    th.join(10)


def test_disagreement_with_the_grpc_fed_stream_book(venue):
    b, gate, th = grpc_books(venue, [book(symbol=SLUG, bids=((970, 100),),
                                          offers=((990, 100),))])
    r = SB.sample(SLUG, record=AEC, books_current=b.current,
                  retail_read=fake_retail(retail_md(
                      bids=(("0.59", "1"),), offers=(("0.60", "1"),))))
    assert r["verdict"] == SB.V_DISAGREE
    assert r["verdict_reason"] == "BEST_BID_DIFFERS"
    assert r["stream_changed_in_window"] is False
    assert r["matched_stream_read"] is None
    assert r["diff"]["levels"][0]["stream"] == ["0.97", "1"]
    gate.set()
    th.join(10)


def test_a_sample_outside_the_window_is_not_comparable():
    b = books_with()

    class Clock:
        t = time.time()

        def __call__(self):
            self.t += 0.4        # four reads of the clock -> 1.2 s window
            return self.t
    r = SB.sample(SLUG, record=AEC, books_current=lambda s, now=None:
                  b.current(s, now=time.time()),
                  retail_read=fake_retail(retail_md()), clock=Clock())
    assert r["verdict"] == SB.V_NC and r["verdict_reason"] == SB.NC_WINDOW
    assert r["within_window"] is False and r["window_s"] > SB.MAX_WINDOW_S


def test_identity_not_exact_reads_nothing():
    calls = []
    rec = dict(AEC, symbol="aec-mlb-nyy-bos-2026-10-04")
    r = SB.sample(SLUG, record=rec, books_current=books_with().current,
                  retail_read=fake_retail(retail_md(), calls=calls))
    assert r["verdict"] == SB.V_NC and r["verdict_reason"] == SB.NC_IDENTITY
    assert r["identity_ok"] is False and calls == []
    r = SB.sample(SLUG, record=None, books_current=books_with().current,
                  retail_read=fake_retail(retail_md(), calls=calls))
    assert r["identity_refusal"] and calls == []


def test_retail_unreadable_and_stream_not_current_are_not_comparable():
    b = books_with()
    r = SB.sample(SLUG, record=AEC, books_current=b.current,
                  retail_read=fake_retail(ok=False, error="ReadTimeout"))
    assert (r["verdict"], r["verdict_reason"]) == (SB.V_NC, SB.NC_RETAIL)
    assert r["retail_error"] == "ReadTimeout"
    b.on_disconnected("test")
    r = SB.sample(SLUG, record=AEC, books_current=b.current,
                  retail_read=fake_retail(retail_md()))
    assert (r["verdict"], r["verdict_reason"]) == (SB.V_NC, SB.NC_STREAM)
    assert r["stream_refusal"] == IS.R_GAP_CONNECTION


def test_a_stream_change_inside_the_window_matches_the_read_it_agrees_with():
    b = books_with(bids=((440, 1000),))

    def retail_read(slug):
        # the book moves on the stream while the retail read is in flight
        b.on_update({"symbol": SLUG, "bids": [(450, 1000)],
                     "offers": [(470, 500)], "state": "INSTRUMENT_STATE_OPEN",
                     "transact_time": time.time()})
        return {"ok": True, "marketData": retail_md(), "error": None}
    r = SB.sample(SLUG, record=AEC, books_current=b.current,
                  retail_read=retail_read)
    assert r["stream_changed_in_window"] is True
    assert r["verdict"] == SB.V_AGREE and r["matched_stream_read"] == "after"


# ── §3 the retail read ──────────────────────────────────────────────────

def mock_client(seen):
    from polymarket_us import PolymarketUS

    def handler(request):
        seen.append((request.method, request.url.host, request.url.path,
                     dict(request.headers)))
        return httpx.Response(200, json={"marketData": retail_md()})
    return SB.install_read_only(PolymarketUS(),
                                inner=httpx.MockTransport(handler))


def test_the_retail_read_is_one_public_get_of_the_documented_book_path():
    seen = []
    r = SB.retail_book_read(SLUG, client=mock_client(seen))
    assert r["ok"] is True and r["marketData"]["bids"][0]["qty"] == "10"
    assert [(m, h, p) for m, h, p, _ in seen] == [
        ("GET", "gateway.polymarket.us", "/v1/markets/%s/book" % SLUG)]
    hdrs = {k.lower() for k in seen[0][3]}
    assert not hdrs & {"x-pm-access-key", "x-pm-signature", "authorization"}


def test_any_other_method_is_refused_before_it_is_sent():
    seen = []
    c = mock_client(seen)
    with pytest.raises(Exception):
        c.post("/v1/orders", body={"marketSlug": SLUG})
    with pytest.raises(Exception):
        c.delete("/v1/orders/x")
    assert seen == []
    t = SB.ReadOnlyTransport(httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(SB.WriteRefused):
        t.handle_request(httpx.Request("POST", "https://x/v1/orders"))


def test_a_bad_slug_is_never_sent():
    seen = []
    for bad in ("", "../orders", "A-UPPER", "x/y", None):
        r = SB.retail_book_read(bad, client=mock_client(seen))
        assert r == {"ok": False, "marketData": None, "error": "SLUG_REFUSED"}
    assert seen == []


def test_the_default_client_is_keyless_and_wrapped_read_only(monkeypatch):
    monkeypatch.setenv("PMUS_KEY_ID", "NOT-REAL")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    c = SB._keyless_client()
    assert c.key_id is None and c.secret_key is None
    assert isinstance(c._http._transport, SB.ReadOnlyTransport)
    # a proxy mount is wrapped too: no route around the read-only gate
    mounts = [t for t in c._http._mounts.values() if t is not None]
    assert mounts and all(isinstance(t, SB.ReadOnlyTransport) for t in mounts)


# ── §4 persistence and the worker helper ────────────────────────────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, sql, *a):
        return await self.conn.execute(sql, *a)

    async def fetch(self, sql, *a):
        return await self.conn.fetch(sql, *a)


@pg
async def test_probe_rows_persist_and_orders_placed_is_checked_to_zero():
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        b = books_with()

        class Store:
            def instrument(self, s):
                return {"record": AEC} if s == SLUG else None
        calls = []
        out = await WMD.probe_same_book(
            _Pool(conn), Store(), [SLUG, "aec-nfl-x-y-2026-10-04"],
            process_id="institutional_md:test:1:abc", current=b.current,
            retail_read=fake_retail(retail_md(), calls=calls),
            # the book was just written: the quiet bound (retail cache
            # horizon) is the pacing test's subject, not this one's
            quiet_s=0.0, gate=lambda: {"blocking": False})
        assert out["samples"] == 2 and out["written"] == 2
        assert out["by"] == {SB.V_AGREE: 1, SB.V_NC: 1}
        assert calls == [SLUG]          # the unmapped symbol read nothing
        rows = await conn.fetch(
            "SELECT * FROM institutional_same_book_probe WHERE process_id="
            "'institutional_md:test:1:abc' ORDER BY symbol")
        assert [r["verdict"] for r in rows] == [SB.V_AGREE, SB.V_NC]
        agree = dict(rows[0])
        assert agree["orders_placed"] == 0 and agree["within_window"]
        assert json.loads(agree["stream_book"])["bids"][0]["price"] == "0.45"
        assert agree["stream_venue_ts"] is not None
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO institutional_same_book_probe (process_id, "
                    "service, symbol, retail_slug, identity_ok, verdict, "
                    "orders_placed) VALUES ('p','s','x','x',false,"
                    "'NOT_COMPARABLE',1)")
    finally:
        await tx.rollback()
        await conn.close()


def test_persist_never_raises():
    class Broken:
        async def execute(self, *a):
            raise RuntimeError("down")
    assert asyncio.run(SB.persist(Broken(), [{"symbol": "x"}],
                                  process_id="p", service="s")) == 0
