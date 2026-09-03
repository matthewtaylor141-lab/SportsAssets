"""The taker flag (to-a-tee program Phase 8, owner order 2026-09-02,
"I want us to match everything ... mirror the whales to a tee").
Measurement only: no rule keys on trades.taker.

What is pinned and why:

  * the census is OFF by default and OFF for every existing caller: the
    walk's requests and statements are byte-identical to the golden
    recorded on the unmodified reconciler (the money-path rule: a switch
    that is off changes nothing)
  * with the census on, the walk's own statements are STILL byte-
    identical — the census adds statements, it never edits one
  * ONE extra request per whale per walk, takerOnly=true, one page, in
    the walk (never the poller: data_api_max_rps is shared, config:53)
  * intersection by dedupe_key, per served row (raw-identical twins of
    one taker bundle both count), true for the matched rows and false
    ONLY for the walk's rows strictly inside the page's own time span
    that did not match — a short page is not read as "no older taker
    fills" (a truncated page from a degraded index would brand every
    older fill maker with a 200)
  * NOTHING written under the 0.9 floor: if takerOnly aggregates a
    taker order over N makers the sizes differ, no key matches, and the
    column stays NULL by design rather than reading maker=false
  * and when the page clears the floor WITH an aggregated row on it
    (the taker unit's adversarial review, the major): the legs of that
    taker order share its transaction, and a walk row whose tx is on
    the taker page under another key reads NULL — never false (it is
    his taker sweep), never true (the intersection is by key)
  * one request per whale per walk through polite_get (the shared
    throttle), issued after that whale's walk, whatever the roster size
  * the census row is written every walk, wrote=false with a reason on
    a 4xx, a timeout, a non-list body, an empty page or a failed walk
  * a false never overwrites a true (absence from a page is the weaker
    proof), and a census failure never fails the walk
  * SPAN TESTIMONY IS VOID under a misordered page or a dirty walk
    (the taker unit's second adversarial review, the major): a false
    is a span claim, and the page's reach was a bare min() over its ts
    — the walk's own round-21 failure shape. One late-indexed old
    taker row mid-page branded 672 of 682 walk rows maker=false at
    match_rate 1.0, and a walk the feed had already proven degraded
    (dirty) still wrote false. The page now verifies newest-first
    with the walk's cumulative floor (round 42, not the adjacent-row
    check a ramp evades), the walk's dirty counter is read (a missing
    cov row reads dirty), and under either only the true keys are
    written, with the census naming the reason in `span_void`
  * THE PAGE'S TAIL AND THE WALK AS BORDER WITNESS (the taker unit's
    third adversarial review, the major): the cumulative check fires
    at a successor and the page's last row has none, so a late-indexed
    old row served LAST floored the span again (672 of 682 false at
    rate 1.0, the folded shape one row further down). The review's fix
    — drop the one tail row from the floor — moves the shape one row
    up (two old rows ordered among themselves at the tail floor it the
    same way), so the census applies the walk's round-21 (b) with the
    walk itself as the border witness: the floor is the oldest row at
    or above the LAST MATCHED row in page order (a matched row is one
    the clean walk served with ordered successors verified below it);
    the unmatched tail counts for the rate and testifies for nothing.
    Walk rows below the deepest matched taker fill read NULL (lost
    measurement), never false
  * WHICH MATCHES THE WALK VERIFIED (the taker unit's fourth
    adversarial review, the major): "matched" alone is not "walk-
    verified" — the walk's cumulative check fires at a successor and
    the walk has a tail with none (the border page's last row, or a
    complete walk's short final page). A late-indexed old row served
    THERE kept the walk clean and, once the taker page served it too,
    matched by key and floored the span 30 days down (582 of 682 false,
    92 below the page's reach). The census now floors only from
    matched rows the walk's own testimony covers: at or above
    cov["oldest"] (walk_floor — cap rows have the border page's ordered
    successors below them; border rows never extend oldest and never
    floor) AND strictly newer than the walk's oldest served second (a
    complete walk's last row, which nothing verified). A match below
    either line counts for the rate and reads true; it floors nothing.
    walk_floor None (a caller that cannot say) proves no span at all
  * PAGE DIRTY (the same review, the other major): an unreadable page
    row — the walk's round-11 stub of one fill — dropped its tx with
    it, so a size-0 or hash-less stub of his sweep's aggregate let the
    legs read false at rate 0.9. An unreadable row, or a row dated past
    the walk's own wallclock + FUTURE_SKEW_S (round 40), now voids the
    span (span_void=page_dirty) and still counts against the rate; the
    void reasons rank walk_dirty, then page_misordered, then page_dirty
  * the switch reads config's `reconcile_taker_census` when the field
    exists (config.py is not this unit's file: the integrating phase
    adds it), else the env var that field will read,
    RECONCILE_TAKER_CENSUS, and only an explicit yes turns it on
  * the migration is 049 (the program register's number for
    trades.taker; 048 is Phase 0b's)
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from types import SimpleNamespace

import httpx

import sportsassets.ingestion.poller as poller_mod
import sportsassets.ingestion.reconciler as rec
from sportsassets.ingestion.poller import parse_data_api_trade

WALLET = "0x" + "ab" * 20
TOP_TS = int(time.time()) - 3600
STEP = 10


def _raw(ts: int, i: int, size: float = 10.0) -> dict:
    return {
        "transactionHash": "0x" + format(i + 1, "064x"),
        "asset": str(10_000 + i),
        "side": "BUY",
        "size": size,
        "price": 0.5,
        "timestamp": ts,
    }


def _feed(n: int = 700) -> list[dict]:
    return [_raw(TOP_TS - i * STEP, i) for i in range(n)]


def _key(raw: dict) -> str:
    return parse_data_api_trade(raw, 7, "w").dedupe_key


class _Resp:
    def __init__(self, page, status: int = 200):
        self._page = page
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            req = httpx.Request("GET", "http://feed.test/trades")
            raise httpx.HTTPStatusError(
                "%d" % self._status, request=req,
                response=httpx.Response(self._status, request=req))

    def json(self):
        return self._page


class _RecordingPool:
    """Every statement with its args, in order, plus the heartbeat."""

    def __init__(self, fail_on: str | None = None):
        self.stmts: list[tuple] = []
        self.fail_on = fail_on

    def _rec(self, kind, sql, a):
        self.stmts.append((kind, sql, list(a)))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("db refused")

    async def fetchval(self, sql, *a, timeout=None):
        self._rec("fetchval", sql, a)
        if "abs(extract(epoch" in sql:
            return None
        return 1

    async def fetch(self, sql, *a, timeout=None):
        self._rec("fetch", sql, a)
        return [{"id": 7, "address": WALLET, "username": "w"}]

    async def execute(self, sql, *a, timeout=None):
        self._rec("execute", sql, a)

    @property
    def details(self):
        for kind, sql, a in self.stmts:
            if "reconciliation_runs SET" in sql:
                return json.loads(a[2])
        return None

    def taker_writes(self) -> list[tuple]:
        return [(sql, a) for _k, sql, a in self.stmts
                if sql.startswith("UPDATE trades SET taker")]

    def census(self, key: str = rec.TAKER_CENSUS_KEY) -> dict | None:
        rows = [a for _k, sql, a in self.stmts
                if "INSERT INTO ingestion_state" in sql and a[0] == key]
        return json.loads(rows[-1][1]) if rows else None

    def walk_stmts(self) -> list[tuple]:
        """The statements that are the walk's own (not the census's)."""
        return [(k, sql, a) for k, sql, a in self.stmts
                if "taker" not in sql and "ingestion_state" not in sql]


def _wire(monkeypatch, feed, taker_page=None, taker_status: int = 200,
          taker_raise: Exception | None = None, settings_extra: dict | None = None,
          pool: _RecordingPool | None = None):
    pool = pool or _RecordingPool()
    calls: list[dict] = []
    beats: list[tuple] = []

    async def fake_get(http, path, params=None):
        calls.append(dict(params))
        if params.get("takerOnly") == "true":
            if taker_raise is not None:
                raise taker_raise
            return _Resp(taker_page, taker_status)
        return _Resp(feed[params["offset"]:params["offset"] + 100])

    async def fake_pool():
        return pool

    async def fake_hb(name, status, detail=None):
        beats.append((name, status, detail))

    async def fake_sport(cond):
        return None

    async def fake_ingest(ev, notify=True):
        return (1, False)

    monkeypatch.setattr(rec, "polite_get", fake_get)
    monkeypatch.setattr(rec, "get_pool", fake_pool)
    monkeypatch.setattr(rec, "heartbeat", fake_hb)
    monkeypatch.setattr(rec, "_sport_for_condition", fake_sport)
    monkeypatch.setattr(rec, "ingest_trade_result", fake_ingest)
    ns = {"data_api_base": "http://feed.test", **(settings_extra or {})}
    monkeypatch.setattr(rec, "settings", lambda: SimpleNamespace(**ns))
    return pool, calls, beats


def _taker_calls(calls):
    return [c for c in calls if c.get("takerOnly") == "true"]


def _walk_calls(calls):
    return [c for c in calls if c.get("takerOnly") == "false"]


# ------------------------------------------------- the switch is off

def test_default_off_issues_no_taker_request_and_writes_nothing(monkeypatch):
    """Every existing caller (workers/reconciler.py calls
    reconcile_once() bare) gets the walk as it was: no takerOnly=true
    request, no trades.taker write, no census row."""
    pool, calls, beats = _wire(monkeypatch, _feed(), taker_page=[])
    asyncio.run(rec.reconcile_once(depth=500))
    assert _taker_calls(calls) == []
    assert pool.taker_writes() == []
    assert pool.census() is None
    assert [k for k, sql, a in pool.stmts if "ingestion_state" in sql] == []
    assert beats[-1][1] == "ok"


def test_the_config_switch_turns_it_on_and_an_explicit_false_wins(monkeypatch):
    """`reconcile_taker_census` on settings (absent today; the
    integrating phase adds it) turns the census on without touching
    the worker's call site; an explicit keyword overrides config."""
    pool, calls, _ = _wire(monkeypatch, _feed(), taker_page=[],
                           settings_extra={"reconcile_taker_census": True})
    asyncio.run(rec.reconcile_once(depth=500))
    assert len(_taker_calls(calls)) == 1
    assert pool.census() is not None

    pool, calls, _ = _wire(monkeypatch, _feed(), taker_page=[],
                           settings_extra={"reconcile_taker_census": True})
    asyncio.run(rec.reconcile_once(depth=500, taker_census=False))
    assert _taker_calls(calls) == [] and pool.census() is None


def test_the_walk_is_byte_identical_with_the_census_on(monkeypatch):
    """The census ADDS statements and one request; it edits none. The
    walk's own statements (with args) and its walk requests are equal
    with the switch on and off, and the extra request is exactly one
    takerOnly=true page at offset 0."""
    off_pool, off_calls, off_beats = _wire(monkeypatch, _feed(), taker_page=[])
    off = asyncio.run(rec.reconcile_once(depth=500))
    on_pool, on_calls, on_beats = _wire(monkeypatch, _feed(), taker_page=[])
    on = asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert off == on
    assert off_beats == on_beats
    assert on_pool.walk_stmts() == off_pool.stmts == off_pool.walk_stmts()
    assert _walk_calls(on_calls) == off_calls == _walk_calls(off_calls)
    assert _taker_calls(on_calls) == [{
        "user": WALLET, "limit": rec.TAKER_PAGE, "offset": 0, "takerOnly": "true"}]
    # and the census request comes AFTER the walk's last page, never
    # inside it (one extra request per whale per walk, at the end)
    assert on_calls[-1]["takerOnly"] == "true"
    assert all(c["takerOnly"] == "false" for c in on_calls[:-1])


# ------------------------------------------------ the intersection

def test_intersection_by_dedupe_key_short_page_labels_false_only_inside_its_span(monkeypatch):
    """Ten of the walk's own rows come back on a SHORT takerOnly page:
    those ten are written true; false goes to the walk's rows strictly
    newer than the page's oldest WALK-VERIFIED taker row (feed[584],
    the cap's last row — feed[640] is a border row and floors nothing,
    the fourth review) that did not match; the walk rows older than
    that stay NULL — a short page is NOT read as "no older taker
    fills" (a degraded index serves a truncated page with a 200, the
    walk's own round-7 lesson)."""
    feed = _feed()
    picks = [3, 50, 120, 121, 300, 333, 480, 499, 584, 640]   # 584: cap tail; 640: border
    taker_page = [dict(feed[i]) for i in picks]
    pool, calls, _ = _wire(monkeypatch, feed, taker_page=taker_page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    writes = pool.taker_writes()
    assert len(writes) == 2
    (sql_t, a_t), (sql_f, a_f) = writes
    assert sql_t == "UPDATE trades SET taker = true WHERE dedupe_key = ANY($1::text[])"
    assert a_t == [sorted(_key(feed[i]) for i in picks)]
    assert sql_f == ("UPDATE trades SET taker = false "
                     "WHERE dedupe_key = ANY($1::text[]) AND taker IS NULL")
    # the walk ingested rows 0..681 (pages at 0,97,...,582 under the
    # overlap pagination; the last page serves 582..681 and is the
    # border page: cov oldest is feed[584]); the page's span reaches
    # down to feed[584], the deepest walk-verified match: rows 0..583
    # not matched are makers, rows 585..681 are outside the verified
    # reach — NULL (feed[640] reads true and floors nothing)
    cov = pool.details["per_wallet"]["cov:" + WALLET]
    assert cov["oldest"] == float(feed[584]["timestamp"]) and not cov["complete"]
    in_span = {_key(feed[i]) for i in range(584)}
    assert set(a_f[0]) == in_span - set(a_t[0])
    assert len(a_f[0]) == 584 - 8
    below = {_key(feed[i]) for i in range(585, 682)}
    assert not below & set(a_f[0])
    assert below & set(a_t[0]) == {_key(feed[640])}, "the border match reads true"
    c = pool.census()
    assert c["whale"] == WALLET and c["taker_page_rows"] == 10
    assert c["matched"] == 10 and c["match_rate"] == 1.0
    assert c["wrote"] is True and c["reason"] is None
    assert c["labeled_true"] == 10 and c["labeled_false"] == 576
    assert c["ambiguous"] == 0
    assert isinstance(c["at"], str) and c["at"].endswith("Z")


def test_full_page_labels_false_only_strictly_newer_than_its_oldest_row(monkeypatch):
    """A FULL page (TAKER_PAGE rows) proves nothing about fills older
    than its oldest WALK-VERIFIED row, and that row's own second may
    be half-served: false goes only to walk rows STRICTLY newer than
    it. Here the page's two deepest rows (feed[588], feed[594]) are
    border rows: true, and no floor (the fourth review) — the floor is
    feed[582], the deepest cap match."""
    feed = _feed()
    picks = list(range(0, 600, 6))                     # 100 rows, oldest = feed[594]
    assert len(picks) == rec.TAKER_PAGE
    taker_page = [dict(feed[i]) for i in picks]
    pool, _, _ = _wire(monkeypatch, feed, taker_page=taker_page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert set(a_t[0]) == {_key(feed[i]) for i in picks}
    taker_lo = feed[582]["timestamp"]
    expect_false = {_key(feed[i]) for i in range(682)
                    if feed[i]["timestamp"] > taker_lo and i not in picks}
    assert set(a_f[0]) == expect_false
    # a row at exactly the page's oldest second is not labeled either way
    assert all(feed[i]["timestamp"] != taker_lo or i in picks for i in range(682))
    c = pool.census()
    assert c["match_rate"] == 1.0 and c["wrote"] is True


def test_an_aggregated_taker_row_does_not_match_and_the_floor_refuses(monkeypatch):
    """The floor's reason: if takerOnly=true aggregates one taker order
    over N maker legs, its size differs from every leg the walk served,
    no key matches. 8 of 10 matching (0.8) writes NOTHING and says
    under_floor; 9 of 10 (0.9) clears the floor and writes."""
    feed = _feed()
    picks = [3, 50, 120, 121, 300, 333, 480, 499, 584, 640]
    page = [dict(feed[i]) for i in picks]
    page[0]["size"] = 30.0          # two legs of 10 + 10 + 10 aggregated
    page[1]["size"] = 20.0
    pool, _, beats = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.taker_writes() == [], "under the floor nothing is written"
    c = pool.census()
    assert c["taker_page_rows"] == 10 and c["matched"] == 8
    assert c["match_rate"] == 0.8 and c["wrote"] is False
    assert c["reason"] == "under_floor"
    assert c["labeled_true"] == 0 and c["labeled_false"] == 0
    assert beats[-1][1] == "ok", "a refused census never colours the walk"

    page = [dict(feed[i]) for i in picks]
    page[0]["size"] = 30.0
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    c = pool.census()
    assert c["match_rate"] == 0.9 and c["wrote"] is True
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert len(a_t[0]) == 9
    # the aggregated row's leg is unmatched INSIDE the span but shares
    # the row's TRANSACTION: it is his taker order in another shape,
    # so it reads NULL — never false, never true (the review's major)
    assert _key(feed[3]) not in a_f[0] and _key(feed[3]) not in a_t[0]
    assert c["ambiguous"] == 1 and c["labeled_true"] == 9
    assert c["labeled_false"] == len(a_f[0])


def _ladder(feed: list[dict], at: int, prices: tuple[float, ...]) -> list[dict]:
    """Rewrite feed[at:at+n] as N legs of ONE taker order: one tx, one
    second, one asset, ascending cents — F1's own shape (0.46, 0.46,
    0.47 inside one second on the read book). The legs carry distinct
    sizes (each maker's resting quantity), so they are distinct trades
    rows, not raw-identical twins."""
    legs = []
    for j, px in enumerate(prices):
        leg = dict(feed[at])
        leg["price"] = px
        leg["size"] = 10.0 + 2.0 * j
        feed[at + j] = leg
        legs.append(leg)
    return legs


def test_a_taker_order_aggregated_over_n_makers_reads_null_not_false(monkeypatch):
    """The review's major, in F1's shape: the walk serves his sweep as
    three legs (one tx, one second, 0.46/0.46/0.47); the takerOnly
    page serves it as ONE row (size 30, vwap) plus nine exact matches,
    so the page clears the floor at 9/10. The three legs are absent
    from the page by key — but they ARE the taker order: they read
    NULL, and only the walk's other rows inside the span read false."""
    feed = _feed()
    legs = _ladder(feed, 200, (0.46, 0.46, 0.47))
    picks = [3, 50, 120, 121, 300, 333, 480, 499, 584, 640]
    agg = dict(legs[0])
    agg["size"] = 36.0                  # 10 + 12 + 14
    agg["price"] = 0.4639
    # the page is served newest-first, as the venue serves it (the
    # second review: a page out of order proves no span at all), so
    # the aggregate sits in its own second between feed[121] and
    # feed[300]
    page = [dict(feed[i]) for i in picks[:4]] + [agg] + \
        [dict(feed[i]) for i in picks[4:9]]
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    c = pool.census()
    assert c["taker_page_rows"] == 10 and c["matched"] == 9
    assert c["match_rate"] == 0.9 and c["wrote"] is True
    assert c["ambiguous"] == 3
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    leg_keys = {_key(leg) for leg in legs}
    assert len(leg_keys) == 3, "three distinct legs (twins collapse; these do not)"
    assert not leg_keys & set(a_t[0]), "never true on a tx-only match"
    assert not leg_keys & set(a_f[0]), "never false: it is his taker sweep"
    assert _key(feed[199]) in a_f[0] and _key(feed[203]) in a_f[0], \
        "the neighbours of the sweep inside the span still read maker"
    assert len(a_t[0]) == 9


def test_rn1_shape_two_aggregates_over_ladders_writes_nothing(monkeypatch):
    """The read book: 2 rows on the takerOnly page, both aggregates of
    same-second ladders the walk served as legs. No key matches, the
    rate is 0/2, the floor refuses, NOTHING is written, and the census
    says under_floor — the column stays NULL rather than branding the
    ladders (68% of his dollars) maker=false."""
    feed = _feed()
    l1 = _ladder(feed, 100, (0.46, 0.46, 0.47))
    l2 = _ladder(feed, 400, (0.45, 0.45, 0.46))
    a1, a2 = dict(l1[0]), dict(l2[0])
    a1["size"] = a2["size"] = 36.0
    a1["price"], a2["price"] = 0.4639, 0.4539
    pool, _, beats = _wire(monkeypatch, feed, taker_page=[a1, a2])
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.taker_writes() == []
    c = pool.census()
    assert c["taker_page_rows"] == 2 and c["matched"] == 0
    assert c["match_rate"] == 0.0 and c["wrote"] is False
    assert c["reason"] == "under_floor"
    # no row on the page is one the walk served, so the page proves no
    # span at all (the third review: the floor is walk-verified) —
    # nothing is inside it to be ambiguous, and no leg could ever read
    # false by any route
    assert c["ambiguous"] == 0
    assert beats[-1][1] == "ok"
    # and the pure reading of the same page: the six legs are in NO
    # list, and there is no span to put them in
    walk = {_key(r): (float(r["timestamp"]), rec._tx_ident(r["transactionHash"]))
            for r in feed[:682]}
    page = [(_key(a1), float(a1["timestamp"]), rec._tx_ident(a1["transactionHash"])),
            (_key(a2), float(a2["timestamp"]), rec._tx_ident(a2["transactionHash"]))]
    res = rec.taker_intersection(walk, page, walk_floor=float(feed[584]["timestamp"]))
    leg_keys = {_key(r) for r in l1 + l2}
    assert len(leg_keys) == 6
    assert res["false_keys"] == [] and res["true_keys"] == []
    assert res["ambiguous_keys"] == [] and res["span_void"] is None


def test_taker_rows_outside_the_walk_reach_are_not_eligible():
    """Pure: taker rows older than the walk's oldest second, or newer
    than its newest row (filled between the walk and the page), are
    rows the walk never had its chance at — neither for nor against."""
    walk = {"k%d" % i: (float(1000 - i), "t%d" % i) for i in range(10)}   # 991..1000
    rows = [("new", 2000.0, "tnew"),           # filled after the walk
            ("k1", 999.0, "t1"), ("k4", 996.0, "t4"),
            ("tie", 991.0, "ttie"),            # the oldest second, unmatched
            ("old", 500.0, "told")]            # below the reach
    # (in page order, newest-first: the second review voids the span
    # of a page that is not; the walk's floor is its oldest second —
    # every row a cap row, as on a complete walk)
    res = rec.taker_intersection(walk, rows, walk_floor=991.0)
    assert res["span_void"] is None
    assert res["eligible"] == 2 and res["matched"] == 2
    assert res["match_rate"] == 1.0
    assert res["true_keys"] == ["k1", "k4"]
    # the page's span reaches down to its last MATCHED row (k4 at 996,
    # the third review): the tie and the old row below it are the
    # unmatched tail, verified by nothing, so k5..k9 read NULL
    assert res["false_keys"] == ["k0", "k2", "k3"]
    assert res["ambiguous_keys"] == []
    # a match AT the oldest second is proof by itself and counts
    res = rec.taker_intersection(walk, [("k9", 991.0, "t9")], walk_floor=991.0)
    assert res["eligible"] == 1 and res["matched"] == 1


def test_false_is_written_only_strictly_inside_the_pages_span():
    """Pure: whatever the page's length, a walk row at or below the
    page's floor is outside its reach and reads NULL; rows newer than
    the walk's newest are inside the reach (the page was asked after
    the walk, from offset 0). The floor is the last walk-verified row
    (the third review): a full page of unmatched rows below it is the
    unverified tail and extends nothing."""
    walk = {"k%d" % i: (float(1000 - i), "t%d" % i) for i in range(10)}   # 991..1000
    res = rec.taker_intersection(walk, [("k5", 995.0, "t5")], walk_floor=991.0)
    assert res["true_keys"] == ["k5"]
    assert res["false_keys"] == ["k0", "k1", "k2", "k3", "k4"]
    # a full page of rows the walk never served below it changes
    # nothing: the span is the verified chain's, not the page's length
    res = rec.taker_intersection(
        walk, [("k5", 995.0, "t5")] + [("x%d" % j, 100.0 - j, "tx%d" % j)
                                       for j in range(rec.TAKER_PAGE - 1)],
        walk_floor=991.0)
    assert res["true_keys"] == ["k5"]
    assert res["false_keys"] == ["k0", "k1", "k2", "k3", "k4"], \
        "the 99 unmatched rows below k5 are the unverified tail"
    assert res["span_void"] is None


def test_a_walk_row_sharing_a_tx_with_a_taker_row_reads_null():
    """Pure, the review's major: a walk row unmatched by key whose
    TRANSACTION appears on the taker page is a leg of a taker order
    served in another shape — NULL, not false, not true. Whether the
    page's row for that tx matched (the aggregate happened to equal
    one leg) or not (it equalled none) makes no difference."""
    # z is an older taker fill the walk served too (matched, a cap row
    # above the walk's floor with the border row w below it, so it
    # floors the span — the third and fourth reviews): the span covers
    # a, b and c
    walk = {"a": (999.0, "0xt"), "b": (999.0, "0xt"), "c": (998.0, "0xu"),
            "z": (500.0, "0xz"), "w": (400.0, "0xw")}
    floor = ("z", 500.0, "0xz")
    res = rec.taker_intersection(walk, [("a", 999.0, "0xt"), floor], walk_floor=500.0)
    assert res["true_keys"] == ["a", "z"] and res["false_keys"] == ["c"]
    assert res["ambiguous_keys"] == ["b"]
    res = rec.taker_intersection(walk, [("agg", 999.0, "0xt"), floor], walk_floor=500.0)
    assert res["eligible"] == 2 and res["matched"] == 1 and res["match_rate"] == 0.5
    assert res["true_keys"] == ["z"] and res["false_keys"] == ["c"]
    assert res["ambiguous_keys"] == ["a", "b"]
    # the identity is the dedupe key's own fold: case never splits a tx
    walk = {"a": (999.0, rec._tx_ident("0xAbC ")), "c": (998.0, "0xu"),
            "z": (500.0, "0xz"), "w": (400.0, "0xw")}
    res = rec.taker_intersection(walk, [("agg", 999.0, rec._tx_ident(" 0XABC")), floor],
                                 walk_floor=500.0)
    assert res["ambiguous_keys"] == ["a"] and res["false_keys"] == ["c"]
    # a walk row outside the span shares the tx: NULL anyway, not listed
    walk = {"a": (999.0, "0xt"), "old": (900.0, "0xt"), "c": (998.0, "0xu"),
            "y": (950.0, "0xy")}
    res = rec.taker_intersection(walk, [("agg", 999.0, "0xt"), ("y", 950.0, "0xy")],
                                 walk_floor=900.0)
    assert res["ambiguous_keys"] == ["a"] and res["false_keys"] == ["c"]
    assert res["true_keys"] == ["y"]


def test_unreadable_taker_rows_count_against_the_rate():
    """Fail closed: a page row the parser or the validity gate refused
    is eligible and unmatched — it lowers the rate, never raises it."""
    walk = {"k%d" % i: (float(1000 - i), "t%d" % i) for i in range(10)}
    rows = [("k1", 999.0, "t1"), (None, None, None), (None, None, None)]
    res = rec.taker_intersection(walk, rows, walk_floor=991.0)
    assert res["eligible"] == 3 and res["matched"] == 1
    assert abs(res["match_rate"] - 1 / 3) < 1e-9
    # and it voids the span (the third review): k0 is inside the reach
    # and absent, but a page with a stub on it testifies for nothing
    assert res["true_keys"] == ["k1"] and res["false_keys"] == []
    assert res["span_void"] == "page_dirty"


def test_raw_identical_twins_both_count_as_matched():
    """Equal legs of one same-second taker bundle collapse to ONE trades
    row under the dedupe key; both served legs must count, or the most
    taker-shaped page there is would fail the floor."""
    walk = {"k0": (1000.0, "t0"), "k1": (999.0, "t1"), "k2": (998.0, "t2")}
    res = rec.taker_intersection(walk, [("k1", 999.0, "t1"), ("k1", 999.0, "t1")],
                                 walk_floor=998.0)
    assert res["eligible"] == 2 and res["matched"] == 2 and res["match_rate"] == 1.0
    # k0 is inside the page's span (newer than its oldest row) and
    # absent: maker; k2 is below the page's oldest row: NULL
    assert res["true_keys"] == ["k1"] and res["false_keys"] == ["k0"]


def test_a_page_with_no_readable_ts_proves_no_span():
    walk = {"k1": (999.0, "t1"), "k2": (998.0, "t2")}
    res = rec.taker_intersection(walk, [(None, None, None)] * rec.TAKER_PAGE)
    assert res["match_rate"] == 0.0 and res["false_keys"] == []
    assert res["ambiguous_keys"] == []


def test_empty_walk_rows_yield_nothing():
    res = rec.taker_intersection({}, [("k1", 1.0, "t1")])
    assert res == {"eligible": 0, "matched": 0, "match_rate": None,
                   "true_keys": [], "false_keys": [], "ambiguous_keys": [],
                   "span_void": None}


# ------------------------------------ span testimony void (second review)

def test_a_misordered_taker_page_writes_true_only(monkeypatch):
    """The second review's major, from its probe: nine exact matches
    and ONE late-indexed row 30 days old in the middle of the page
    (round 21's durable feed property). The old row is below the
    walk's reach, so it is not eligible and the rate stays 1.0; the
    bare min() floor read it as the page's reach and branded 672 of
    682 walk rows maker=false, 96 of them below the walk's own last
    page. Now: the nine are written true, NO false is written, and
    the census says span_void=page_misordered."""
    feed = _feed()
    page = [dict(feed[i]) for i in (3, 50, 120, 121, 300)]
    old = dict(feed[660])
    old["timestamp"] = feed[660]["timestamp"] - 86400 * 30
    page.insert(3, old)
    page += [dict(feed[i]) for i in (333, 480, 499, 584)]
    ts = [r["timestamp"] for r in page]
    assert any(ts[i] > min(ts[:i]) + rec.ORDER_TOL_S for i in range(1, len(ts))), \
        "the page fails the walk's own ordering check"
    pool, calls, beats = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert len(_taker_calls(calls)) == 1
    writes = pool.taker_writes()
    assert len(writes) == 1, "the true write only"
    (sql_t, a_t), = writes
    assert sql_t == "UPDATE trades SET taker = true WHERE dedupe_key = ANY($1::text[])"
    assert sorted(a_t[0]) == sorted(_key(feed[i]) for i in
                                    (3, 50, 120, 121, 300, 333, 480, 499, 584))
    assert len(a_t[0]) == 9
    c = pool.census()
    assert c["taker_page_rows"] == 10 and c["matched"] == 9
    assert c["match_rate"] == 1.0 and c["wrote"] is True and c["reason"] is None
    assert c["labeled_true"] == 9 and c["labeled_false"] == 0
    assert c["ambiguous"] == 0 and c["span_void"] == "page_misordered"
    # the walk itself was clean: the page's disorder is the page's
    assert pool.details["per_wallet"]["cov:" + WALLET]["dirty"] == 0
    assert beats[-1][1] == "ok"


def test_a_dirty_walk_writes_true_only(monkeypatch):
    """The second review's other probe: the walk serves one row NEWER
    than its predecessor by 5000 s (an inversion the walk records as
    dirty=1 and whose coverage claim the sweep already refuses). The
    taker page is nine exact matches. The census used to write 576
    false labels off a walk whose timestamps the feed had disowned;
    now the nine are written true, no false, span_void=walk_dirty."""
    feed = _feed()
    feed[300]["timestamp"] = feed[300]["timestamp"] + 5000    # inverted, not future
    assert feed[300]["timestamp"] < time.time() + rec.FUTURE_SKEW_S
    # a dirty walk skips the border page (r21), so its last page is
    # 485..584 and feed[584] is the walk's oldest row: nine matches
    # inside the walk's reach
    picks = [3, 50, 120, 121, 333, 480, 499, 560, 584]
    pool, _, beats = _wire(monkeypatch, feed, taker_page=[dict(feed[i]) for i in picks])
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.details["per_wallet"]["cov:" + WALLET]["dirty"] == 1
    writes = pool.taker_writes()
    assert len(writes) == 1
    (sql_t, a_t), = writes
    assert sql_t.startswith("UPDATE trades SET taker = true")
    assert sorted(a_t[0]) == sorted(_key(feed[i]) for i in picks)
    c = pool.census()
    assert c["taker_page_rows"] == 9 and c["matched"] == 9
    assert c["match_rate"] == 1.0 and c["wrote"] is True and c["reason"] is None
    assert c["labeled_true"] == 9 and c["labeled_false"] == 0
    assert c["ambiguous"] == 0 and c["span_void"] == "walk_dirty"
    assert beats[-1][1] == "ok"


def test_the_page_ordering_check_is_the_walks_cumulative_floor():
    """Pure. The check is the walk's round-42 CUMULATIVE floor, not the
    adjacent-row comparison the review's fix text proposed: a re-index
    ramp climbing back in steps each under ORDER_TOL_S passes every
    adjacent check with unbounded inversion, exactly what round 42
    killed in the walk. Same-second bundles and jitter inside the
    tolerance keep the span; one step past the running minimum voids
    it. Unreadable rows carry no ts and take no part in the check
    (they void the span on their own, and the misordered reason ranks
    above theirs — the third review's order)."""
    walk = {"k%d" % i: (float(10000 - 10 * i), "t%d" % i) for i in range(100)}  # 9010..10000
    walk["z"] = (9500.0, "0xz")          # the floor row is one the walk served
    floor = ("z", 9500.0, "0xz")
    wf = {"walk_floor": 9010.0}          # the walk's floor: its oldest cap second
    # the ramp: 9500 then +100 five times (each adjacent step < 120,
    # the cumulative rise 500 > 120)
    ramp = [("r%d" % j, 9500.0 + 100.0 * j, "0xr%d" % j) for j in range(1, 6)]
    res = rec.taker_intersection(walk, [("k10", 9900.0, "t10"), floor] + ramp, **wf)
    assert res["true_keys"] == ["k10", "z"]
    assert res["false_keys"] == [] and res["ambiguous_keys"] == []
    assert res["span_void"] == "page_misordered"
    assert res["eligible"] == 7 and res["matched"] == 2, \
        "the five ramp rows are eligible and unmatched"
    # jitter inside the tolerance and a same-second bundle: still ordered
    fine = [("k10", 9900.0, "t10"), ("y", 9890.0, "0xy"), ("y2", 9890.0, "0xy2"),
            ("y3", 9890.0 + rec.ORDER_TOL_S, "0xy3"), floor]
    res = rec.taker_intersection(walk, fine, **wf)
    assert res["span_void"] is None
    assert set(res["false_keys"]) == {k for k, (ts, _t) in walk.items() if ts > 9500.0} - {"k10"}
    # one row past the running minimum, however far from its neighbour;
    # the unreadable row beside it voids too, and misordered is the
    # reason named (walk_dirty, then page_misordered, then page_dirty)
    res = rec.taker_intersection(
        walk, [("k10", 9900.0, "t10"), floor, (None, None, None),
               ("late", 9500.0 + rec.ORDER_TOL_S + 1, "0xl")], **wf)
    assert res["span_void"] == "page_misordered" and res["false_keys"] == []
    res = rec.taker_intersection(
        walk, [("k10", 9900.0, "t10"), floor, (None, None, None),
               ("late", 9500.0 + rec.ORDER_TOL_S + 1, "0xl")], walk_dirty=True, **wf)
    assert res["span_void"] == "walk_dirty" and res["false_keys"] == []
    # and a misordered page with NO match still writes nothing at all
    # (the second row sits ORDER_TOL_S + 1 above the first)
    res = rec.taker_intersection(
        walk, [("a", 9900.0, "0xa"), ("b", 9900.0 + rec.ORDER_TOL_S + 1, "0xb")], **wf)
    assert res["true_keys"] == [] and res["false_keys"] == []
    assert res["span_void"] == "page_misordered"
    # while a second row exactly ORDER_TOL_S above is jitter, not inversion
    res = rec.taker_intersection(
        walk, [("a", 9900.0, "0xa"), ("b", 9900.0 + rec.ORDER_TOL_S, "0xb")], **wf)
    assert res["span_void"] is None


def test_walk_dirty_voids_the_span_and_keeps_the_true_keys():
    """Pure. `walk_dirty=True` (the walk's own counter) leaves the
    matched keys as proof and withholds every span claim — false and
    ambiguous alike — naming walk_dirty; the default is False so the
    pure reading of a page is unchanged for every existing pin."""
    walk = {"a": (999.0, "0xt"), "b": (999.0, "0xt"), "c": (998.0, "0xu"),
            "z": (500.0, "0xz"), "w": (400.0, "0xw")}
    # z: a walk-served taker fill above the walk's floor, the border
    # row w below it (third and fourth reviews)
    floor = ("z", 500.0, "0xz")
    clean = rec.taker_intersection(walk, [("a", 999.0, "0xt"), floor], walk_floor=500.0)
    assert clean["false_keys"] == ["c"] and clean["ambiguous_keys"] == ["b"]
    assert clean["span_void"] is None
    dirty = rec.taker_intersection(walk, [("a", 999.0, "0xt"), floor], walk_dirty=True,
                                   walk_floor=500.0)
    assert dirty["true_keys"] == ["a", "z"] and dirty["match_rate"] == 1.0
    assert dirty["false_keys"] == [] and dirty["ambiguous_keys"] == []
    assert dirty["span_void"] == "walk_dirty"
    # the floor still refuses first: nothing is written under it
    under = rec.taker_intersection(walk, [("agg", 999.0, "0xt"), floor], walk_dirty=True,
                                   walk_floor=500.0)
    assert under["match_rate"] == 0.5 and under["true_keys"] == ["z"]
    assert under["span_void"] == "walk_dirty"
    # a page with no readable ts proves no span either way, and names
    # no void reason (there was nothing to void)
    res = rec.taker_intersection(walk, [(None, None, None)], walk_dirty=True,
                                 walk_floor=500.0)
    assert res["span_void"] is None and res["false_keys"] == []


# ------------------ the page's tail and a dirty page (third review)

def test_a_tail_old_taker_row_never_sets_the_span(monkeypatch):
    """The third review's probe: nine exact matches and ONE 30-day-old
    late-indexed row served LAST. The cumulative check fires only at a
    successor and the tail has none, so the bare min() floored the
    span 30 days down and 672 of 682 walk rows read false at rate 1.0
    (96 of them below the walk's own last page). Now the floor is the
    last matched row (feed[584]): false goes exactly to the walk rows
    strictly newer than it that did not match, none below it, and the
    span is not void (the page is ordered — the old row is simply the
    unverified tail)."""
    feed = _feed()
    picks = [3, 50, 120, 121, 300, 333, 480, 499, 584]
    page = [dict(feed[i]) for i in picks]
    old = dict(feed[660])
    old["timestamp"] = feed[660]["timestamp"] - 86400 * 30
    page.append(old)
    pool, calls, beats = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.details["per_wallet"]["cov:" + WALLET]["dirty"] == 0
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert sorted(a_t[0]) == sorted(_key(feed[i]) for i in picks)
    expect_false = {_key(feed[i]) for i in range(584) if i not in picks}
    assert set(a_f[0]) == expect_false and len(a_f[0]) == 584 - 8
    below = {_key(feed[i]) for i in range(584, 682)}
    assert not below & set(a_f[0]), "nothing at or below feed[584] reads maker"
    c = pool.census()
    assert c["taker_page_rows"] == 10 and c["matched"] == 9
    assert c["match_rate"] == 1.0 and c["wrote"] is True
    assert c["labeled_true"] == 9 and c["labeled_false"] == 576
    assert c["span_void"] is None and c["ambiguous"] == 0
    assert beats[-1][1] == "ok"


def test_a_tail_old_row_on_a_full_page_never_sets_the_span(monkeypatch):
    """The probe's full-page variant: 99 exact matches (oldest feed[588])
    and the 30-day-old row as the 100th. false stops at feed[582]: the
    deepest CAP match — feed[588] is a border row and floors nothing
    (the fourth review)."""
    feed = _feed()
    picks = list(range(0, 594, 6))                     # 99 rows, oldest feed[588]
    old = dict(feed[660])
    old["timestamp"] = feed[660]["timestamp"] - 86400 * 30
    page = [dict(feed[i]) for i in picks] + [old]
    assert len(page) == rec.TAKER_PAGE
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert len(a_t[0]) == 99
    expect_false = {_key(feed[i]) for i in range(582) if i not in picks}
    assert set(a_f[0]) == expect_false and len(a_f[0]) == 582 - 97
    assert not {_key(feed[i]) for i in range(582, 682)} & set(a_f[0])
    c = pool.census()
    assert c["match_rate"] == 1.0 and c["wrote"] is True and c["span_void"] is None


def test_the_floor_is_the_last_matched_row_not_the_tail_minus_one():
    """Pure. The review's own fix — drop the ONE tail row from the
    floor — is not enough: two late-indexed old rows ordered among
    themselves at the tail pass the cumulative check and the second-
    to-last row floors the span exactly as the last one did. The
    census's border witness is the walk: the floor is the oldest row
    at or above the last MATCHED row in page order, and however long
    the unmatched tail, it testifies for nothing."""
    walk = {"k%d" % i: (float(10000 - 10 * i), "t%d" % i) for i in range(100)}  # 9010..10000
    wf = {"walk_floor": 9010.0}          # the walk's floor: its oldest cap second
    # one old row last (the probe)
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("k2", 9980.0, "t2"),
                                        ("old", 100.0, "0xold")], **wf)
    assert res["span_void"] is None and res["true_keys"] == ["k1", "k2"]
    assert res["false_keys"] == ["k0"], "only the walk rows newer than k2"
    # two old rows last, ordered among themselves: the review's fix
    # would floor at 200 and label k3..k99 false; the walk-verified
    # floor still stops at k2
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("k2", 9980.0, "t2"),
                                        ("old2", 200.0, "0xold2"), ("old1", 100.0, "0xold1")],
                                 **wf)
    assert res["span_void"] is None and res["false_keys"] == ["k0"]
    # and a whole tail of them
    tail = [("old%d" % j, 1000.0 - j, "0xold%d" % j) for j in range(50)]
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("k2", 9980.0, "t2")] + tail,
                                 **wf)
    assert res["span_void"] is None and res["false_keys"] == ["k0"]
    # a two-row page and a one-row page: the matched row is verified by
    # the walk's own ordered successors below it, so it floors — the
    # rows above it read maker, nothing below it does
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("old", 100.0, "0xold")], **wf)
    assert res["false_keys"] == ["k0"] and res["span_void"] is None
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1")], **wf)
    assert res["false_keys"] == ["k0"] and res["span_void"] is None
    res = rec.taker_intersection(walk, [("k0", 10000.0, "t0")], **wf)
    assert res["true_keys"] == ["k0"] and res["false_keys"] == []
    # an unmatched row ABOVE the last match is inside the verified
    # chain and counts against the rate; it floors nothing by itself
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("u", 9985.0, "0xu"),
                                        ("k3", 9970.0, "t3"), ("old", 100.0, "0xold")], **wf)
    assert res["eligible"] == 3 and res["matched"] == 2
    assert res["false_keys"] == ["k0", "k2"]
    # a page with no matched row proves no span at all
    res = rec.taker_intersection(walk, [("u", 9985.0, "0xu"), ("old", 100.0, "0xold")], **wf)
    assert res["false_keys"] == [] and res["ambiguous_keys"] == []
    assert res["span_void"] is None and res["match_rate"] == 0.0
    # jitter above the last match still floors at the running minimum:
    # a row 1 s older than the match, above it on the page, is the floor
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("j", 9979.0, "0xj"),
                                        ("k2", 9980.0, "t2"), ("old", 100.0, "0xold")], **wf)
    assert res["span_void"] is None and res["false_keys"] == ["k0"]
    assert res["eligible"] == 3 and res["matched"] == 2


def test_a_stub_of_the_aggregate_row_voids_the_span(monkeypatch):
    """The third review's probe B, the walk's round-11 shape: his sweep
    is three legs of one tx at feed[200]; the takerOnly page serves the
    aggregate as a size-0 stub (tx intact) beside nine exact matches.
    The stub is refused by the validity gate, its tx went with it, the
    rate is 9/10 = 0.9 and the page clears the floor — and the three
    legs used to read false. Now an unreadable row makes the whole
    span unwritable: one write (the nine true), zero false, ambiguous
    0, span_void=page_dirty, the walk untouched."""
    feed = _feed()
    legs = _ladder(feed, 200, (0.46, 0.46, 0.47))
    picks = [3, 50, 120, 121, 300, 333, 480, 499, 584]
    leg_keys = {_key(leg) for leg in legs}
    for stub_shape in ("size0", "hashless"):
        feed = _feed()
        legs = _ladder(feed, 200, (0.46, 0.46, 0.47))
        stub = dict(legs[0])
        if stub_shape == "size0":
            stub["size"] = 0
        else:
            stub["size"] = 36.0
            stub["transactionHash"] = ""
        page = [dict(feed[i]) for i in picks[:4]] + [stub] + \
            [dict(feed[i]) for i in picks[4:]]
        pool, _, beats = _wire(monkeypatch, feed, taker_page=page)
        asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
        writes = pool.taker_writes()
        assert len(writes) == 1, stub_shape
        (sql_t, a_t), = writes
        assert sql_t.startswith("UPDATE trades SET taker = true")
        assert sorted(a_t[0]) == sorted(_key(feed[i]) for i in picks)
        assert not leg_keys & set(a_t[0])
        c = pool.census()
        assert c["taker_page_rows"] == 10 and c["matched"] == 9
        assert c["match_rate"] == 0.9 and c["wrote"] is True and c["reason"] is None
        assert c["labeled_true"] == 9 and c["labeled_false"] == 0
        assert c["ambiguous"] == 0 and c["span_void"] == "page_dirty"
        assert pool.details["per_wallet"]["cov:" + WALLET]["dirty"] == 0
        assert beats[-1][1] == "ok"


def test_a_future_dated_page_row_voids_the_span(monkeypatch):
    """The third review's probe D, the walk's round-40 shape: a row
    seven days in the future at the page HEAD has no predecessor for
    the ordering check to see and used to be ignored (span intact, 576
    false). It is feed corruption: it counts against the rate (9/10)
    and voids the span."""
    feed = _feed()
    picks = [3, 50, 120, 121, 300, 333, 480, 499, 584]
    fut = dict(feed[3])
    fut["timestamp"] = int(time.time()) + 7 * 86400
    fut["transactionHash"] = "0x" + "f" * 64
    page = [fut] + [dict(feed[i]) for i in picks]
    pool, _, beats = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    writes = pool.taker_writes()
    assert len(writes) == 1
    assert writes[0][0].startswith("UPDATE trades SET taker = true")
    c = pool.census()
    assert c["taker_page_rows"] == 10 and c["matched"] == 9
    assert c["match_rate"] == 0.9 and c["wrote"] is True
    assert c["labeled_false"] == 0 and c["span_void"] == "page_dirty"
    assert beats[-1][1] == "ok"


def test_page_dirty_pure_and_the_future_bound_is_the_walks():
    """Pure. An unreadable row keeps the true keys and voids the span
    with page_dirty; a row dated past `now` + FUTURE_SKEW_S is the
    same (strictly past: the walk's own bound), and it counts against
    the rate; exactly at the bound it is a row like any other. The
    void reasons rank walk_dirty, page_misordered, page_dirty."""
    # k0..k9 are the cap (991..1000), k10 the border row below them
    walk = {"k%d" % i: (float(1000 - i), "t%d" % i) for i in range(11)}   # 990..1000
    wf = {"walk_floor": 991.0}
    floor = ("k9", 991.0, "t9")
    res = rec.taker_intersection(walk, [("k1", 999.0, "t1"), (None, None, None), floor], **wf)
    assert res["true_keys"] == ["k1", "k9"] and res["false_keys"] == []
    assert res["ambiguous_keys"] == [] and res["span_void"] == "page_dirty"
    assert res["eligible"] == 3 and res["matched"] == 2
    now = 5000.0
    fut = ("f", now + rec.FUTURE_SKEW_S + 1, "0xf")
    res = rec.taker_intersection(walk, [fut, ("k1", 999.0, "t1"), floor], now=now, **wf)
    assert res["span_void"] == "page_dirty" and res["false_keys"] == []
    assert res["eligible"] == 3 and res["matched"] == 2, "the future row counts against"
    edge = ("e", now + rec.FUTURE_SKEW_S, "0xe")
    res = rec.taker_intersection(walk, [edge, ("k1", 999.0, "t1"), floor], now=now, **wf)
    assert res["span_void"] is None, "exactly at the bound is not future"
    assert res["eligible"] == 2, "newer than the walk's newest: neither for nor against"
    assert res["false_keys"] == ["k0", "k2", "k3", "k4", "k5", "k6", "k7", "k8"]
    # the default `now` is the clock: a row a year ahead is future
    res = rec.taker_intersection(walk, [("f", time.time() + 365 * 86400, "0xf"),
                                        ("k1", 999.0, "t1"), floor], **wf)
    assert res["span_void"] == "page_dirty"
    # the order of reasons
    misordered = [("k1", 999.0, "t1"), floor, (None, None, None),
                  ("late", 991.0 + rec.ORDER_TOL_S + 1, "0xl")]
    assert rec.taker_intersection(walk, misordered, **wf)["span_void"] == "page_misordered"
    assert rec.taker_intersection(walk, misordered, walk_dirty=True,
                                  **wf)["span_void"] == "walk_dirty"
    assert rec.taker_intersection(walk, [("k1", 999.0, "t1"), (None, None, None)],
                                  walk_dirty=True, **wf)["span_void"] == "walk_dirty"
    # a page with no readable ts still names no reason: no testimony
    assert rec.taker_intersection(walk, [(None, None, None)], **wf)["span_void"] is None


# ---------------- which matches the walk verified (fourth review)

def test_the_walks_own_tail_row_never_floors_the_span(monkeypatch):
    """The fourth review's probe F: the late-indexed 30-day-old row is
    the walk's OWN last served row — feed[681], the border page's
    tail, ingested into walk_rows with no successor to verify it and
    never extending cov oldest, so the walk stays clean (dirty 0, cov
    oldest feed[584]). The takerOnly page serves it too, LAST, beside
    99 exact matches: it matched by key, last_match reached it, and
    the span floored 30 days down — 582 false, 92 of them below the
    page's reach, every one sticky. Now: 100 true (presence is proof),
    false exactly the walk rows strictly newer than feed[582] — the
    deepest CAP match; feed[588] is a border row and floors nothing —
    that did not match, none at or below it, span not void."""
    feed = _feed()
    feed[681]["timestamp"] = feed[681]["timestamp"] - 86400 * 30
    picks = list(range(0, 594, 6))                     # 99 rows, oldest feed[588]
    page = [dict(feed[i]) for i in picks] + [dict(feed[681])]
    assert len(page) == rec.TAKER_PAGE
    pool, _, beats = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    cov = pool.details["per_wallet"]["cov:" + WALLET]
    assert cov["dirty"] == 0 and not cov["complete"]
    assert cov["oldest"] == float(feed[584]["timestamp"]), "the walk never saw it"
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert sorted(a_t[0]) == sorted(_key(feed[i]) for i in picks + [681])
    expect_false = {_key(feed[i]) for i in range(582) if i not in picks}
    assert set(a_f[0]) == expect_false and len(a_f[0]) == 582 - 97
    assert not {_key(feed[i]) for i in range(582, 682)} & set(a_f[0]), \
        "nothing at or below the deepest cap match reads maker"
    c = pool.census()
    assert c["taker_page_rows"] == 100 and c["matched"] == 100
    assert c["match_rate"] == 1.0 and c["wrote"] is True and c["reason"] is None
    assert c["labeled_true"] == 100 and c["labeled_false"] == 485
    assert c["span_void"] is None and c["ambiguous"] == 0
    assert beats[-1][1] == "ok"
    # the short-page variant (probe F2): nine matches and the walk's
    # old tail row — 10 true, false stops at feed[584]
    feed = _feed()
    feed[681]["timestamp"] = feed[681]["timestamp"] - 86400 * 30
    picks = [3, 50, 120, 121, 300, 333, 480, 499, 584]
    page = [dict(feed[i]) for i in picks] + [dict(feed[681])]
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert len(a_t[0]) == 10 and _key(feed[681]) in a_t[0]
    assert set(a_f[0]) == {_key(feed[i]) for i in range(584) if i not in picks}
    assert len(a_f[0]) == 576
    assert not {_key(feed[i]) for i in range(584, 682)} & set(a_f[0])
    assert pool.census()["span_void"] is None


def test_two_old_rows_at_the_walks_tail_never_floor(monkeypatch):
    """Probe F3: two late-indexed old rows ordered among themselves at
    the walk's tail (feed[680], feed[681]) — the second verifies the
    first for the walk's cumulative check, nothing verifies the second,
    and both sit below cov oldest. Both on the page, both matched, both
    true; neither floors: false stops at feed[584]."""
    feed = _feed()
    feed[680]["timestamp"] = feed[680]["timestamp"] - 86400 * 30
    feed[681]["timestamp"] = feed[681]["timestamp"] - 86400 * 30 - 10
    picks = [3, 50, 120, 121, 300, 333, 480, 499, 584]
    page = [dict(feed[i]) for i in picks] + [dict(feed[680]), dict(feed[681])]
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.details["per_wallet"]["cov:" + WALLET]["dirty"] == 0
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert sorted(a_t[0]) == sorted(_key(feed[i]) for i in picks + [680, 681])
    assert set(a_f[0]) == {_key(feed[i]) for i in range(584) if i not in picks}
    assert not {_key(feed[i]) for i in range(584, 682)} & set(a_f[0])
    c = pool.census()
    assert c["matched"] == 11 and c["match_rate"] == 1.0
    assert c["labeled_false"] == 576 and c["span_void"] is None


def test_a_cap_row_floors_and_a_border_row_never_does(monkeypatch):
    """The partial (capped) walk, genuine feed: cov oldest is feed[584],
    the cap's last row, verified by the border page's ordered rows
    below it. A matched cap row at the cap's own tail floors; matched
    border rows (feed[585], the first past the cap; feed[681], the
    walk's last served row) read true and extend the floor by nothing
    — the walk never testified for them."""
    feed = _feed()
    page = [dict(feed[i]) for i in (3, 584, 585, 681)]
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    cov = pool.details["per_wallet"]["cov:" + WALLET]
    assert cov["oldest"] == float(feed[584]["timestamp"]) and not cov["complete"]
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert sorted(a_t[0]) == sorted(_key(feed[i]) for i in (3, 584, 585, 681))
    assert set(a_f[0]) == {_key(feed[i]) for i in range(584) if i != 3}
    assert not {_key(feed[i]) for i in range(584, 682)} & set(a_f[0])
    assert pool.census()["labeled_false"] == 583
    # the same page against a walk whose deepest matched CAP row sits
    # higher: the floor follows the cap match, never the border ones
    page = [dict(feed[i]) for i in (3, 499, 585, 681)]
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert set(a_f[0]) == {_key(feed[i]) for i in range(499) if i != 3}
    assert not {_key(feed[i]) for i in range(499, 682)} & set(a_f[0])


def test_a_complete_walks_final_page_never_extends_the_floor(monkeypatch):
    """Probe G, the complete walk: a 560-row feed ends inside the page
    at 485 (short, complete=True), and cov oldest is that page's last
    row feed[559] — which the walk itself never verified with a
    successor. When feed[559] is the late-indexed old row and the page
    serves it beside eight matches, it read as the floor (551 false,
    59 below feed[499]). Now the walk's oldest served second never
    floors: false stops at feed[499], the deepest verified match.
    And on a genuine feed the same holds for a genuine last row: it
    reads true and the second-to-last row (verified by it) floors."""
    feed = _feed(560)
    feed[559]["timestamp"] = feed[559]["timestamp"] - 86400 * 30
    picks = [3, 50, 120, 121, 300, 333, 480, 499]
    page = [dict(feed[i]) for i in picks] + [dict(feed[559])]
    pool, _, beats = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    cov = pool.details["per_wallet"]["cov:" + WALLET]
    assert cov["complete"] is True and cov["dirty"] == 0
    assert cov["oldest"] == float(feed[559]["timestamp"]), \
        "the walk's own coverage claim trusts its tail; the census does not"
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert sorted(a_t[0]) == sorted(_key(feed[i]) for i in picks + [559])
    assert set(a_f[0]) == {_key(feed[i]) for i in range(499) if i not in picks}
    assert len(a_f[0]) == 499 - 7
    assert not {_key(feed[i]) for i in range(499, 560)} & set(a_f[0])
    c = pool.census()
    assert c["matched"] == 9 and c["match_rate"] == 1.0 and c["wrote"] is True
    assert c["labeled_false"] == 492 and c["span_void"] is None
    assert beats[-1][1] == "ok"
    # genuine feed, the last two rows on the page: feed[559] is the
    # unverified tail (true, no floor); feed[558] floors
    feed = _feed(560)
    page = [dict(feed[i]) for i in picks] + [dict(feed[558]), dict(feed[559])]
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.details["per_wallet"]["cov:" + WALLET]["complete"] is True
    (sql_t, a_t), (sql_f, a_f) = pool.taker_writes()
    assert _key(feed[559]) in a_t[0] and _key(feed[558]) in a_t[0]
    assert set(a_f[0]) == {_key(feed[i]) for i in range(558) if i not in picks}
    assert _key(feed[559]) not in a_f[0]


def test_a_match_below_the_walks_floor_counts_and_reads_true_but_floors_nothing():
    """Pure. k0..k99 are the cap (walk_floor 9010), b1/b2 the border
    rows below it, `tail` the walk's last served row. A matched border
    row counts for the rate and reads true and floors nothing; a page
    whose matches all sit below the floor proves no span; walk_floor
    None proves no span (fail closed); and the walk's oldest served
    second never floors even when walk_floor names it (the complete
    walk's cov oldest IS that unverified tail)."""
    walk = {"k%d" % i: (float(10000 - 10 * i), "t%d" % i) for i in range(100)}  # 9010..10000
    walk["b1"] = (9000.0, "0xb1")
    walk["b2"] = (8990.0, "0xb2")
    walk["tail"] = (100.0, "0xtail")
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("b1", 9000.0, "0xb1")],
                                 walk_floor=9010.0)
    assert res["eligible"] == 2 and res["matched"] == 2 and res["match_rate"] == 1.0
    assert res["true_keys"] == ["b1", "k1"]
    assert res["false_keys"] == ["k0"] and res["span_void"] is None
    # all matches below the floor: true, and no span
    res = rec.taker_intersection(walk, [("b1", 9000.0, "0xb1"), ("b2", 8990.0, "0xb2")],
                                 walk_floor=9010.0)
    assert res["true_keys"] == ["b1", "b2"] and res["match_rate"] == 1.0
    assert res["false_keys"] == [] and res["ambiguous_keys"] == []
    assert res["span_void"] is None
    # the walk's own tail row, matched (probe I): true, no floor
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("tail", 100.0, "0xtail")],
                                 walk_floor=9010.0)
    assert res["true_keys"] == ["k1", "tail"] and res["false_keys"] == ["k0"]
    # ... and still no floor when walk_floor names it (a complete walk's
    # cov oldest is its unverified last row)
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("tail", 100.0, "0xtail")],
                                 walk_floor=100.0)
    assert res["true_keys"] == ["k1", "tail"] and res["false_keys"] == ["k0"]
    res = rec.taker_intersection(walk, [("tail", 100.0, "0xtail")], walk_floor=100.0)
    assert res["true_keys"] == ["tail"] and res["false_keys"] == []
    assert res["span_void"] is None
    # a matched row exactly AT the floor floors (a cap row: verified);
    # one at the walk's oldest second does not, whatever the floor
    res = rec.taker_intersection(walk, [("k99", 9010.0, "t99")], walk_floor=9010.0)
    assert res["false_keys"] == sorted("k%d" % i for i in range(99))
    # walk_floor None: the caller cannot say what the walk verified —
    # true keys stand, nothing reads maker, no reason named (there was
    # no testimony to void)
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1")])
    assert res["true_keys"] == ["k1"] and res["false_keys"] == []
    assert res["ambiguous_keys"] == [] and res["span_void"] is None
    assert res["match_rate"] == 1.0
    # the documented residual, the walk's own round-22 shape: a run of
    # ordered old rows at the END of a complete feed — each verified
    # only by the next old row — floors the census as it floors the
    # walk's coverage claim. Pinned so the price is on record.
    walk = {"k%d" % i: (float(10000 - 10 * i), "t%d" % i) for i in range(100)}
    walk["o1"] = (200.0, "0xo1")
    walk["o2"] = (100.0, "0xo2")
    res = rec.taker_intersection(walk, [("k1", 9990.0, "t1"), ("o1", 200.0, "0xo1")],
                                 walk_floor=100.0)
    assert res["false_keys"] == sorted("k%d" % i for i in range(100) if i != 1)


# --------------------------------------------- the switch's env fallback

def test_the_env_var_turns_the_census_on_only_as_an_explicit_yes(monkeypatch):
    """config.py is not this unit's file, so until the integrating
    phase adds `reconcile_taker_census` the deployment's handle is the
    env var that field will read. Only an explicit yes turns it on;
    the settings field wins when it exists (it is that env var, parsed
    by config)."""
    for val, on in (("1", True), ("true", True), ("YES", True), (" on ", True),
                    ("0", False), ("false", False), ("", False), ("maybe", False)):
        monkeypatch.setenv(rec.TAKER_CENSUS_ENV, val)
        assert rec._taker_census_switch(SimpleNamespace()) is on, val
        pool, calls, _ = _wire(monkeypatch, _feed(), taker_page=[])
        asyncio.run(rec.reconcile_once(depth=500))
        assert (len(_taker_calls(calls)) == 1) is on, val
        assert (pool.census() is not None) is on, val
    monkeypatch.delenv(rec.TAKER_CENSUS_ENV)
    assert rec._taker_census_switch(SimpleNamespace()) is False
    # the field wins when config carries it
    monkeypatch.setenv(rec.TAKER_CENSUS_ENV, "1")
    assert rec._taker_census_switch(SimpleNamespace(reconcile_taker_census=False)) is False
    monkeypatch.setenv(rec.TAKER_CENSUS_ENV, "0")
    assert rec._taker_census_switch(SimpleNamespace(reconcile_taker_census=True)) is True
    monkeypatch.delenv(rec.TAKER_CENSUS_ENV)


# ------------------------------------------------------ the refusals

def test_a_4xx_writes_the_census_with_wrote_false_and_nothing_else(monkeypatch):
    pool, calls, beats = _wire(monkeypatch, _feed(), taker_page=[], taker_status=403)
    out = asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert len(_taker_calls(calls)) == 1
    assert pool.taker_writes() == []
    c = pool.census()
    assert c["wrote"] is False and c["reason"].startswith("http:")
    assert c["taker_page_rows"] == 0 and c["match_rate"] is None
    # the walk itself is untouched by the refusal
    assert out["missed"] == 0 and "failed:" + WALLET not in out["per_wallet"]
    assert pool.details["per_wallet"]["cov:" + WALLET]["dirty"] == 0
    assert beats[-1] == ("reconciler", "ok", {"missed": 0, "failed": 0})


def test_a_timeout_writes_the_census_with_wrote_false(monkeypatch):
    pool, _, beats = _wire(monkeypatch, _feed(), taker_page=[],
                           taker_raise=httpx.ReadTimeout("slow"))
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.taker_writes() == []
    c = pool.census()
    assert c["wrote"] is False and c["reason"] == "http:ReadTimeout"
    assert beats[-1][1] == "ok"


def test_a_non_list_body_writes_the_census_with_wrote_false(monkeypatch):
    pool, _, _ = _wire(monkeypatch, _feed(), taker_page={"error": "shape"})
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.taker_writes() == []
    c = pool.census()
    assert c["wrote"] is False and c["reason"] == "non_list"


def test_an_empty_taker_page_is_not_proof_of_all_maker(monkeypatch):
    """200-[] from a degraded index must not brand 685 rows maker=false:
    zero eligible rows is no reading (match_rate None), nothing written."""
    pool, _, _ = _wire(monkeypatch, _feed(), taker_page=[])
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert pool.taker_writes() == []
    c = pool.census()
    assert c["wrote"] is False and c["reason"] == "no_eligible_rows"
    assert c["taker_page_rows"] == 0 and c["match_rate"] is None


def test_a_null_page_element_is_one_unreadable_row_not_a_crash(monkeypatch):
    feed = _feed()
    page = [None, dict(feed[3]), dict(feed[50])]
    pool, _, _ = _wire(monkeypatch, feed, taker_page=page)
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    c = pool.census()
    assert c["taker_page_rows"] == 3 and c["matched"] == 2
    assert abs(c["match_rate"] - 2 / 3) < 1e-9 and c["wrote"] is False
    assert c["reason"] == "under_floor"
    # and the null element is an unreadable row: the span is void by
    # name even though the floor already refused (the third review)
    assert c["span_void"] == "page_dirty"
    assert pool.taker_writes() == []


def test_a_failed_walk_gets_a_census_without_a_request(monkeypatch):
    pool = _RecordingPool()
    beats: list[tuple] = []
    calls: list[dict] = []

    async def failing_get(http, path, params=None):
        calls.append(dict(params))
        raise RuntimeError("venue refused")

    async def fake_pool():
        return pool

    async def fake_hb(name, status, detail=None):
        beats.append((name, status, detail))

    monkeypatch.setattr(rec, "polite_get", failing_get)
    monkeypatch.setattr(rec, "get_pool", fake_pool)
    monkeypatch.setattr(rec, "heartbeat", fake_hb)
    monkeypatch.setattr(rec, "settings",
                        lambda: SimpleNamespace(data_api_base="http://feed.test"))
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert _taker_calls(calls) == [], "no page is asked for a walk that failed"
    c = pool.census()
    assert c["wrote"] is False and c["reason"] == "walk_failed"
    assert beats[-1][1] == "error"


def test_a_census_write_failure_never_fails_the_walk(monkeypatch):
    """The census is measurement: its own INSERT failing (or the
    trades UPDATE failing) leaves the walk's result, run row and
    heartbeat exactly as they were."""
    feed = _feed()
    page = [dict(feed[i]) for i in (3, 50)]
    for fail_on in ("ingestion_state", "SET taker = true"):
        pool = _RecordingPool(fail_on=fail_on)
        pool, _, beats = _wire(monkeypatch, feed, taker_page=page, pool=pool)
        out = asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
        assert out["missed"] == 0 and "failed:" + WALLET not in out["per_wallet"]
        assert beats[-1] == ("reconciler", "ok", {"missed": 0, "failed": 0})
        assert pool.details["per_wallet"]["cov:" + WALLET]["dirty"] == 0
    # the trades write that failed leaves the census saying wrote=false
    assert pool.census()["wrote"] is False
    assert pool.census()["reason"] == "db:RuntimeError"


# ------------------------------------------------------- the census row

def test_census_is_written_under_the_literal_key_and_a_per_whale_key(monkeypatch):
    feed = _feed()
    pool, _, _ = _wire(monkeypatch, feed, taker_page=[dict(feed[3])])
    asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    rows = [(sql, a) for _k, sql, a in pool.stmts if "ingestion_state" in sql]
    assert [a[0] for sql, a in rows] == [rec.TAKER_CENSUS_KEY,
                                        rec.TAKER_CENSUS_KEY + ":" + WALLET]
    assert all(sql == ("INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
                       "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
               for sql, a in rows)
    assert rows[0][1][1] == rows[1][1][1]
    c = pool.census()
    assert set(c) == {"at", "whale", "taker_page_rows", "matched", "match_rate",
                      "wrote", "labeled_true", "labeled_false", "ambiguous",
                      "span_void", "reason"}
    assert c["span_void"] is None, "a clean walk and an ordered page"
    assert pool.census(rec.TAKER_CENSUS_KEY + ":" + WALLET) == c


# ------------------------------------------------ the shared budget

WALLET2 = "0x" + "cd" * 20


class _TwoWhalePool(_RecordingPool):
    async def fetch(self, sql, *a, timeout=None):
        self._rec("fetch", sql, a)
        return [{"id": 7, "address": WALLET, "username": "w"},
                {"id": 8, "address": WALLET2, "username": "w2"}]


def test_one_request_per_whale_per_walk_after_that_whales_walk(monkeypatch):
    """The extra load on data_api_max_rps (config:53, shared with the
    poller) is exactly ONE takerOnly=true request per whale per walk —
    never a second page, never a retry loop of its own — and it is
    issued after that whale's own pages, so the walk's cycle grows by
    one throttle slot per whale and nothing else. The walk's own
    statements per whale are the OFF recording."""
    feed = _feed()
    off_pool, off_calls, off_beats = _wire(monkeypatch, feed, taker_page=[],
                                           pool=_TwoWhalePool())
    off = asyncio.run(rec.reconcile_once(depth=500))
    pool, calls, beats = _wire(monkeypatch, feed, taker_page=[dict(feed[3])],
                               pool=_TwoWhalePool())
    on = asyncio.run(rec.reconcile_once(depth=500, taker_census=True))
    assert on == off and beats == off_beats
    assert pool.walk_stmts() == off_pool.stmts
    assert _walk_calls(calls) == off_calls
    taker = _taker_calls(calls)
    assert [c["user"] for c in taker] == [WALLET, WALLET2]
    assert all(c["limit"] == rec.TAKER_PAGE and c["offset"] == 0 for c in taker)
    # each census request sits right after ITS whale's last page
    users = [(c["user"], c["takerOnly"]) for c in calls]
    first_w2 = users.index((WALLET2, "false"))
    assert users[first_w2 - 1] == (WALLET, "true")
    assert users[-1] == (WALLET2, "true")
    assert len(calls) == len(off_calls) + 2
    # one census row per whale, and the literal key carries the last
    assert pool.census(rec.TAKER_CENSUS_KEY + ":" + WALLET)["whale"] == WALLET
    assert pool.census(rec.TAKER_CENSUS_KEY + ":" + WALLET2)["whale"] == WALLET2
    assert pool.census()["whale"] == WALLET2


# ----------------------------------------------- the poller untouched

def test_the_poller_is_untouched_and_the_walk_still_asks_taker_only_false():
    """The census lives in the hourly walk only: the poller's one
    /trades request keeps takerOnly=false and knows nothing of the
    census; the walk's own page request is unchanged."""
    psrc = pathlib.Path(poller_mod.__file__).read_text()
    assert psrc.count("takerOnly") == 1
    assert '"takerOnly": "false"' in psrc
    assert "taker_census" not in psrc and "TAKER" not in psrc
    rsrc = pathlib.Path(rec.__file__).read_text()
    assert rsrc.count('"takerOnly": "false"') == 1, "the walk's page request"
    assert rsrc.count('"takerOnly": "true"') == 1, "the one census request"
    # every request the reconciler makes goes through the shared
    # throttle: the walk's page and the census page, nothing bare
    assert rsrc.count("await polite_get(") == 2
    assert "http.get(" not in rsrc
    # the census sits after the walk's try/except, gated on the switch
    assert "if taker_census:\n                # after the walk, outside its try" in rsrc
    assert rec.TAKER_MATCH_FLOOR == 0.9 and rec.TAKER_PAGE == 100
    assert "AND taker IS NULL" in rsrc, "a false never overwrites a true"
    # the switch's default is fail-closed: None -> the config field when
    # it exists, else the env var it will read, else off
    assert "taker_census = _taker_census_switch(cfg)" in rsrc
    assert 'getattr(cfg, "reconcile_taker_census", None)' in rsrc
    assert 'os.environ.get(TAKER_CENSUS_ENV, "")' in rsrc
    assert rec.TAKER_CENSUS_ENV == "RECONCILE_TAKER_CENSUS"
    # the walk's dirty counter reaches the census, and a MISSING cov
    # row reads dirty (second review): both fail closed by default;
    # the walk's own wallclock is the future bound (third review); the
    # walk's testimony floor, cov oldest, is the line below which no
    # match floors, and its default None proves no span (fourth review)
    assert 'walk_dirty=cov.get("dirty", 1) > 0, now=walk_now,\n' \
           '                    walk_floor=cov.get("oldest"))' in rsrc
    assert "walk_failed: bool, walk_dirty: bool = True,\n" \
           "                        now: float | None = None,\n" \
           "                        walk_floor: float | None = None) -> dict:" in rsrc
    assert "walk_dirty: bool = False,\n" \
           "                       now: float | None = None,\n" \
           "                       walk_floor: float | None = None) -> dict:" in rsrc, \
        "the pure half's defaults"
    assert "walk_dirty=walk_dirty, now=now,\n" \
           "                                         walk_floor=walk_floor)" in rsrc
    assert "if walk_floor is not None and ts >= walk_floor and ts > walk_lo:" in rsrc
    # the I/O half's signature says the shape the intersection unpacks
    assert "walk_rows: dict[str, tuple[float, str]],\n" \
           "                        walk_failed: bool" in rsrc
