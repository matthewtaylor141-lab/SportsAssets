"""Position mirroring, phase P0: the shadow worker (owner order
2026-09-02, "go for it, let's get this working"). Driven with fakes:
no venue, no database. The one invariant above all others: this phase
places NOTHING. Review round one added the venue-load rules: one paced
positions walk per tick, a market we do not hold reads 0 (not
"unreadable"), a failed walk or a run of book misses or a write failure
abandons the tick and backs off, newest markets first under the cap,
and drift is measured against the exit worker's UNPINNED read only
while it is fresh."""
import asyncio
import inspect
import json
import pathlib

from sportsassets.analytics import mirror_report as mr
from sportsassets.workers import mirror_shadow as ms

M, N = "tok-mich", "tok-nak"
CID = "0xcond"
SLUG = "aec-atp-branak-alemic-2026-09-02"


def _fill(asset, side, size, price, ts, **extra):
    d = {"id": ts, "asset": asset, "side": side, "size": size, "price": price, "ts": ts,
         "market_title": "US Open ATP: Brandon Nakashima vs Alex Michelsen",
         "event_slug": "atp-nakashi-michels-2026-09-02", "market_slug": "atp-nakashi-michels-2026-09-02",
         "outcome": "Alex Michelsen" if asset == M else "Brandon Nakashima", "outcome_index": 1}
    d.update(extra)
    return d


class _Pool:
    """Answers the worker's queries by SQL fragment; records writes."""

    def __init__(self, fills=None, ledger_rows=None, snap=None, snap_at=None,
                 whales_ratio_fills=None, switch=None, mapped=True, conds=None,
                 write_raises=False):
        self.fills = fills or []
        self.ledger_rows = ledger_rows or []
        self.snap, self.snap_at = snap, snap_at
        self.ratio_fills = whales_ratio_fills or []
        self.switch = switch
        self.mapped = mapped
        self.conds = conds
        self.write_raises = write_raises
        self.writes = []
        self.state = {}
        self.queries = []

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        self.queries.append((s, a))
        if "SELECT t.condition_id, max(t.ts) AS last_ts" in s:
            if self.conds is not None:
                return [{"condition_id": c} for c in self.conds]
            return [{"condition_id": CID}] if self.fills else []
        if "AS market_title, t.event_slug" in s:             # his_fills
            return list(self.fills)
        if "ORDER BY t.condition_id, t.ts" in s:              # compute_ratio
            return list(self.ratio_fills)
        if "FROM live_orders WHERE asset = ANY($1::text[])" in s:   # map_market
            return ([{"asset": M, "us_market_slug": SLUG, "intent": "ORDER_INTENT_BUY_LONG"}]
                    if self.mapped else [])
        if "WHERE us_market_slug = $1 AND status IN ('filled', 'exiting')" in s:
            return list(self.ledger_rows)
        return []

    async def fetchval(self, sql, *a):
        if "ingestion_state" in sql and a and a[0] == "mirror_shadow":
            return self.switch
        if "ingestion_state" in sql and a and str(a[0]).startswith("whale_positions_raw:"):
            if self.snap is None:
                return None
            return json.dumps({"at": self.snap_at, "partial": False, "sizes": self.snap})
        return None

    async def fetchrow(self, sql, *a):
        return None

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        if "INSERT INTO mirror_shadow" in s and self.write_raises:
            raise RuntimeError('relation "mirror_shadow" does not exist')
        self.writes.append((s, a))
        if "ingestion_state" in sql:
            self.state[a[0]] = a[1]


class _Portfolio:
    def __init__(self, pages, raise_walk=False):
        self.pages, self.raise_walk, self.calls = pages, raise_walk, 0

    def positions(self, q):
        self.calls += 1
        if self.raise_walk:
            raise RuntimeError("429")
        i = int(q.get("cursor") or 0)
        page = self.pages[i]
        return {"positions": page, "nextCursor": str(i + 1) if i + 1 < len(self.pages) else "",
                "eof": i + 1 >= len(self.pages)}


class _Client:
    def __init__(self, portfolio):
        self.portfolio = portfolio


class _Pmus:
    def __init__(self, bid=0.30, ask=0.32, held=None, raise_bbo=False, raise_walk=False,
                 pages=None):
        self.bid, self.ask, self.raise_bbo = bid, ask, raise_bbo
        held = held or {}
        pages = pages or [{s: {"netPosition": v} for s, v in held.items()}]
        self.portfolio = _Portfolio(pages, raise_walk=raise_walk)
        self.calls = []

    def _get_client(self):
        return _Client(self.portfolio)

    def _bbo_quotes(self, client, slug):
        self.calls.append(("bbo", slug))
        if self.raise_bbo:
            raise RuntimeError("venue down")
        return self.bid, self.ask

    # the shadow must never reach for these
    def position_side(self, slug):
        raise AssertionError("the shadow must read positions ONCE per tick, not per market")

    def submit_fok(self, *a, **k):
        raise AssertionError("the shadow placed an order")

    def cancel_order(self, *a, **k):
        raise AssertionError("the shadow cancelled an order")


HIS = [_fill(M, "BUY", 2780, 0.31, 1000), _fill(M, "BUY", 5092.55, 0.30, 1055),
       _fill(M, "BUY", 2011.95, 0.31, 1055), _fill(M, "BUY", 770, 0.31, 1140),
       _fill(N, "BUY", 367.42, 0.77, 1900)]
RATIO = 50.0 / 861.8


def _run(coro):
    return asyncio.run(coro)


def _nosleep(monkeypatch):
    """Record every pacing call instead of sleeping: the worker paces
    each venue read through the process-wide gate (venue_pace.pace)."""
    async def _s(s):
        _nosleep.slept.append(s)
    _nosleep.slept = []
    monkeypatch.setattr(ms, "_sleep", _s)
    monkeypatch.setattr(ms, "pace", lambda s=ms.READ_PACING_S: _nosleep.slept.append(s) or 0.0)
    monkeypatch.setattr(ms.time, "sleep", lambda s: _nosleep.slept.append(("sync", s)))
    return _nosleep.slept


def test_map_market_names_the_long_token_from_our_ledger():
    p = _Pool(fills=HIS)
    m = _run(ms.map_market(p, HIS))
    assert m == {"us_slug": SLUG, "long_asset": M, "other_asset": N, "source": "ledger"}
    assert _run(ms.map_market(p, [])) is None


def test_account_positions_walks_every_page_once_and_names_a_failure(monkeypatch):
    slept = _nosleep(monkeypatch)
    pm = _Pmus(pages=[{"A": {"netPosition": 10}, "B": {"netPosition": -3}},
                      {"C": {"netPosition": 4}, "D": {"netPosition": 2.5}}])
    out = _run(ms.account_positions(pm))
    assert out == {"a": 10.0, "b": -3.0, "c": 4.0, "d": 2.5}
    # two pages, two paced reads: every venue call goes through the gate
    assert pm.portfolio.calls == 2 and slept.count(ms.READ_PACING_S) == 2
    assert _run(ms.account_positions(_Pmus(raise_walk=True))) is None
    # This test used to pass {"netPosition": "bad"} on the second page and
    # assert the walk returned the OTHER three rows -- it pinned the defect
    # (a skipped row in a walk that still called itself complete). The rule
    # now matches the page cap's: see the unreadable-row test below.


def test_shadow_market_reads_his_book_and_plans_a_buy_at_his_level_without_ordering(monkeypatch):
    slept = _nosleep(monkeypatch)
    p = _Pool(fills=HIS)
    pm = _Pmus(bid=0.30, ask=0.32)
    snap = {M: 10654.5, N: 367.42}
    row = _run(ms.shadow_market(p, pm, "rn1", CID, RATIO, snap, positions={}, snap_age_s=40.0))
    assert row["us_market_slug"] == SLUG
    assert row["his_long"] == 10654.5 and row["his_other"] == 367.42
    assert row["his_net"] == round(10654.5 - 367.42, 6)
    assert row["snap_long"] == 10654.5 and row["snap_other"] == 367.42
    assert row["detail"]["snap_age_s"] == 40.0 and "snap_stale" not in row["detail"]
    # a market absent from a successful walk is NOT held: venue 0, not unreadable
    assert row["ledger_net"] == 0 and row["venue_net"] == 0.0
    assert (row["bid"], row["ask"], row["mark"]) == (0.30, 0.32, 0.31)
    # target = ratio x net (596 shares, $185 at the 0.31 mark: under the $250 cap)
    assert row["target"] == int(RATIO * row["his_net"]) == 596 and row["capped"] is False
    assert row["would_side"] == "BUY_LONG" and row["would_qty"] == row["target"]
    assert row["would_px"] == 0.30
    # judged against the NEXT reading (see _write); the immediate read
    # (ask 0.32 > 0.30: not marketable now) rides in the detail
    assert row["would_fill"] is None and row["detail"]["marketable_now"] is False
    assert row["his_last_px"] == 0.31 and row["reason"] == "increase toward target"
    assert pm.calls == [("bbo", SLUG)] and ms.READ_PACING_S in slept   # paced before the read


def test_shadow_market_reduces_at_his_equivalent_price_when_his_net_shrinks(monkeypatch):
    _nosleep(monkeypatch)
    # his burst: 10,000 Nakashima at 0.46 pairs off most of his Michelsen
    fills = HIS + [_fill(N, "BUY", 10000, 0.46, 3300)]
    p = _Pool(fills=fills, ledger_rows=[{"sh": 147.0, "intent": "ORDER_INTENT_BUY_LONG"}])
    pm = _Pmus(bid=0.53, ask=0.55)
    row = _run(ms.shadow_market(p, pm, "rn1", CID, RATIO, {}, positions={SLUG: 147.0}))
    net = round(10654.5 - 367.42 - 10000, 6)
    assert row["his_net"] == net and row["ledger_net"] == 147 and row["venue_net"] == 147.0
    assert row["target"] == int(RATIO * net)
    assert row["would_side"] == "SELL_LONG" and row["would_qty"] == 147 - row["target"]
    # his Nakashima buy at 0.46 is a Michelsen sale at 0.54; the ask is 0.55
    assert row["his_last_px"] == 0.54 and row["would_px"] == 0.55
    assert row["snap_long"] is None                     # no snapshot -> named as None


def test_shadow_market_fails_closed_on_venue_disagreement_and_unreadable_reads(monkeypatch):
    _nosleep(monkeypatch)
    p = _Pool(fills=HIS, ledger_rows=[{"sh": 100.0, "intent": "ORDER_INTENT_BUY_LONG"}])
    row = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, 0.05, {}, positions={SLUG: 40.0}))
    assert row["would_side"] is None and row["reason"].startswith("frozen")
    # the walk failed: venue None -> plan refuses, never guesses
    row1 = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, 0.05, {}, positions=None))
    assert row1["venue_net"] is None and row1["reason"] == "venue unreadable"
    # the book failed: no bid/ask/mark, named in detail
    row2 = _run(ms.shadow_market(p, _Pmus(raise_bbo=True), "rn1", CID, 0.05, {},
                                 positions={SLUG: 100.0}))
    assert row2["bid"] is None and row2["ask"] is None and row2["mark"] is None
    assert row2["detail"]["bbo_error"] == "RuntimeError"
    # a stale snapshot is excluded from drift and flagged
    row3 = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, 0.05, {M: 10654.5},
                                 positions={SLUG: 100.0}, snap_age_s=ms.SNAP_MAX_AGE_S + 1))
    assert row3["snap_long"] is None and row3["detail"]["snap_stale"] is True


def test_an_unmapped_market_is_named_not_invented(monkeypatch):
    _nosleep(monkeypatch)
    p = _Pool(fills=HIS, mapped=False)
    row = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, 0.05, {}, positions={}))
    assert row["reason"].startswith("unmapped") and row.get("us_market_slug") is None
    assert row["his_net"] is None and row["long_asset"] in (M, N)


def _ratio_fills():
    out = []
    for i in range(12):
        out += [{"condition_id": f"c{i}", "asset": f"a{i}", "side": "BUY",
                 "size": 2780.0, "price": 0.31, "ts": 1000.0 + i * 10000}]
    return out


def test_tick_once_walks_positions_once_writes_rows_beats_the_census_and_honours_the_switch(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setenv("MIRROR_WHALES", "RN1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    p = _Pool(fills=HIS, snap={M: 10654.5}, snap_at=ms.time.time() - 10,
              whales_ratio_fills=_ratio_fills())
    pm = _Pmus(bid=0.30, ask=0.32, held={"other-slug": 5.0})
    stats = _run(ms.tick_once(p, pm, now_ts=5000.0))
    assert stats["status"] == "ok" and stats["whales"] == 1 and stats["markets"] == 1
    assert stats["rows"] == 1 and stats["venue_positions"] == 1
    # the plan rests at 0.30 under a 0.32 ask: not marketable now, and
    # nothing earlier to judge -- the census says so in those words
    assert stats["would_orders"] == 1 and stats["marketable_now"] == 0 and stats["unmapped"] == 0
    assert stats["resolved"] == 0 and stats["resolved_filled"] == 0 and "would_fill" not in stats
    assert stats["ratio"]["rn1"] == round(50.0 / (2780.0 * 0.31), 6)
    assert pm.portfolio.calls == 1, "one positions walk per tick"
    ins = [w for w in p.writes if "INSERT INTO mirror_shadow" in w[0]]
    assert len(ins) == 1 and ins[0][1][0] == "rn1" and ins[0][1][1] == CID
    assert "mirror_ratio" in p.state
    # the DB switch stops the tick before any read
    p2 = _Pool(fills=HIS, switch=json.dumps("off"))
    pm2 = _Pmus()
    stats2 = _run(ms.tick_once(p2, pm2, now_ts=5000.0))
    assert stats2.get("switched_off") is True and stats2["rows"] == 0
    assert pm2.portfolio.calls == 0


def test_a_failed_positions_walk_abandons_the_tick_before_any_book_read(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    p = _Pool(fills=HIS)
    pm = _Pmus(raise_walk=True)
    stats = _run(ms.tick_once(p, pm, now_ts=6000.0))
    assert stats["positions_unreadable"] is True and stats["abandoned"] is True
    assert stats["status"] == "degraded" and stats["markets"] == 0 and pm.calls == []
    assert ms._backoff_until == 6000.0 + ms.BACKOFF_S
    assert _run(ms.tick_once(p, _Pmus(), now_ts=6001.0))["skipped_backoff"] is True
    ms._backoff_until = 0.0


def test_a_run_of_book_misses_abandons_the_tick_and_the_cap_reads_newest_first(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    p = _Pool(fills=HIS, conds=[f"c{i}" for i in range(5)])
    stats = _run(ms.tick_once(p, _Pmus(raise_bbo=True), now_ts=7000.0))
    assert stats.get("abandoned") is True and stats["markets"] == 3 and stats["status"] == "degraded"
    assert ms._backoff_until == 7000.0 + ms.BACKOFF_S
    ms._backoff_until = 0.0
    # the cap: newest markets are read, the rest counted as skipped
    monkeypatch.setattr(ms, "MAX_MARKETS_PER_TICK", 2)
    p2 = _Pool(fills=HIS, conds=[f"c{i}" for i in range(5)])
    stats2 = _run(ms.tick_once(p2, _Pmus(), now_ts=8000.0))
    assert stats2["markets"] == 2 and stats2["skipped_markets"] == 3 and stats2["capped_tick"] is True
    q = [s for s, _ in p2.queries if "max(t.ts) AS last_ts" in s][0]
    assert "ORDER BY last_ts DESC" in q


def test_a_write_failure_stops_the_tick_and_degrades_the_heartbeat(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    p = _Pool(fills=HIS, conds=["c1", "c2", "c3"], write_raises=True)
    pm = _Pmus()
    stats = _run(ms.tick_once(p, pm, now_ts=9000.0))
    assert stats["write_failed"] == "RuntimeError" and stats["abandoned"] is True
    assert stats["status"] == "degraded" and stats["markets"] == 1 and stats["rows"] == 0
    assert len(pm.calls) == 1, "no venue budget spent after the first failed write"
    assert ms._backoff_until == 9000.0 + ms.BACKOFF_S
    ms._backoff_until = 0.0


def test_his_level_follows_his_most_recent_move_in_our_direction():
    fills = HIS + [_fill(N, "BUY", 5000, 0.46, 3300), _fill(M, "SELL", 2000, 0.60, 3400)]
    assert ms.his_level(fills, M, N, reducing=False) == 0.31        # his last long BUY
    assert ms.his_level(fills, M, N, reducing=True) == 0.60         # his SELL, newer than 1-0.46
    fills2 = HIS + [_fill(M, "SELL", 2000, 0.60, 3300), _fill(N, "BUY", 5000, 0.46, 3400)]
    assert ms.his_level(fills2, M, N, reducing=True) == 0.54        # the pair completion, newer
    assert ms.his_level(HIS, M, N, reducing=True) == 0.23           # 1 - 0.77
    assert ms.his_level([], M, N, reducing=False) is None


def test_no_ratio_and_no_mark_plan_nothing_and_never_flatten(monkeypatch):
    _nosleep(monkeypatch)
    p = _Pool(fills=HIS, ledger_rows=[{"sh": 147.0, "intent": "ORDER_INTENT_BUY_LONG"}])
    # no ratio (fewer than the minimum markets): a 147-share ledger is NOT flattened
    row = _run(ms.shadow_market(p, _Pmus(bid=0.30, ask=0.32), "rn1", CID, None, {},
                                positions={SLUG: 147.0}))
    assert row["would_side"] is None and row["target"] == 0 and row["reason"].startswith("no ratio")
    # no mark (book unreadable): no target, no uncapped order
    row2 = _run(ms.shadow_market(p, _Pmus(raise_bbo=True), "rn1", CID, RATIO, {},
                                 positions={SLUG: 147.0}))
    assert row2["would_side"] is None and row2["target"] == 0 and row2["reason"].startswith("no mark")
    # a one-sided book (bid only) is no mark either
    row3 = _run(ms.shadow_market(p, _Pmus(bid=0.30, ask=None), "rn1", CID, RATIO, {},
                                 positions={SLUG: 147.0}))
    assert row3["mark"] is None and row3["would_side"] is None


def test_a_plan_is_judged_as_a_resting_order_over_its_life(monkeypatch):
    """A plan fills if the opposite side REACHES its price at any reading
    inside JUDGE_TTL_S, and did not fill if it ages past that life while
    the market is still read. Unobserved stays NULL."""
    _nosleep(monkeypatch)

    class _P(_Pool):
        def __init__(self, counts=None):
            super().__init__(fills=HIS)
            self.counts = counts or {}

        async def execute(self, sql, *a):
            await super().execute(sql, *a)
            for tag, n in self.counts.items():
                if tag in sql:
                    return f"UPDATE {n}"
            return "UPDATE 0"

    p = _P(counts={"/* judge-buy */": 2, "/* judge-expire */": 1})
    res, fil = _run(ms._write(p, {"whale": "rn1", "condition_id": CID, "bid": 0.29, "ask": 0.30,
                                  "detail": {}}))
    assert (res, fil) == (3, 2)
    buy = [w for w in p.writes if "/* judge-buy */" in w[0]][0]
    sell = [w for w in p.writes if "/* judge-sell */" in w[0]][0]
    exp = [w for w in p.writes if "/* judge-expire */" in w[0]][0]
    # a BUY resting at px fills when the ask has come DOWN to px ...
    assert "would_side = 'BUY_LONG'" in buy[0] and "would_px >= $3" in buy[0]
    assert buy[1] == ("rn1", CID, 0.30, ms.JUDGE_TTL_S)
    # ... a SELL when the bid has come UP to px ...
    assert "would_side = 'SELL_LONG'" in sell[0] and "would_px <= $3" in sell[0]
    assert sell[1] == ("rn1", CID, 0.29, ms.JUDGE_TTL_S)
    # ... and only inside the order's life; past it, still read, it did not fill
    assert "interval '1 second')" in buy[0] and "would_fill = false" in exp[0]
    assert "at < now() - ($3::float8 * interval '1 second')" in exp[0]
    assert exp[1] == ("rn1", CID, ms.JUDGE_TTL_S)
    # a level that merely moved away is never counted: no 'bid < px' / 'ask > px' clause
    assert "would_px > $3" not in buy[0] and "would_px < $3" not in sell[0]
    # the side that has to reach us is unread -> that side is not judged
    p2 = _P()
    _run(ms._write(p2, {"whale": "rn1", "condition_id": CID, "bid": None, "ask": 0.30, "detail": {}}))
    assert not [w for w in p2.writes if "/* judge-sell */" in w[0]]
    assert [w for w in p2.writes if "/* judge-buy */" in w[0]]
    # an unreadable book judges nothing at all
    p3 = _P()
    assert _run(ms._write(p3, {"whale": "rn1", "condition_id": CID, "bid": None, "ask": None,
                               "detail": {}})) == (0, 0)
    assert not [w for w in p3.writes if "/* judge-" in w[0]]
    # the report's fill rate is over RESOLVED plans only
    rows = [{"us_market_slug": "s", "would_side": "BUY_LONG", "would_fill": True},
            {"us_market_slug": "s", "would_side": "BUY_LONG", "would_fill": None},
            {"us_market_slug": "s", "would_side": "SELL_LONG", "would_fill": False}]
    out = mr.summarize(rows, rows, {})
    assert out["would_orders"] == 3 and out["would_resolved"] == 2 and out["would_fill_rate"] == 0.5
    assert ms.JUDGE_TTL_S == 600.0


def test_the_ledger_counts_every_sleeve_and_the_windows_are_hours_not_truncated_ints():
    src = inspect.getsource(ms.ledger_net)
    assert "NOT IN ('manual', 'underdog')" not in src
    assert "status IN ('filled', 'exiting')" in src
    assert "($2::float8 * interval '1 hour')" in inspect.getsource(ms.active_conditions)
    assert "($1::float8 * interval '1 hour')" in inspect.getsource(mr.mirror_shadow_report)


def test_the_shadow_never_touches_an_order():
    src = inspect.getsource(ms)
    for banned in ("submit_fok", "cancel_order", "close_position", "execute_manual",
                   "maybe_execute", "mirror_exit", "position_side("):
        assert banned not in src, f"the shadow must not reference {banned}"
    # and the supervisor runs it (source read: importing workers.all pulls
    # every worker's third-party deps into the test process)
    launcher = pathlib.Path(ms.__file__).with_name("all.py").read_text()
    assert '("mirror_shadow", mirror_shadow.main)' in launcher
    assert "mirror_shadow" in launcher.split("LOOPS")[0], "import missing"


def test_the_exit_worker_writes_the_unpinned_read_beside_its_baseline():
    src = pathlib.Path(ms.__file__).with_name("whale_exits.py").read_text()
    assert '_RAW_KEY = "whale_positions_raw:%s"' in src
    i = src.index("await _save(pool, uname.lower(), to_save)")
    assert "_RAW_KEY % uname.lower()" in src[i:i + 1200]
    assert '"sizes": now' in src[i:i + 1200]


def test_the_report_counts_what_gates_phase_one():
    latest = [{"whale": "rn1", "condition_id": "a", "us_market_slug": "s1", "his_long": 100.0,
               "snap_long": 100.0, "would_side": "BUY_LONG", "would_fill": True, "reason": "x",
               "detail": "{}"},
              {"whale": "rn1", "condition_id": "b", "us_market_slug": "s2", "his_long": 110.0,
               "snap_long": 100.0, "would_side": "SELL_LONG", "would_fill": False,
               "reason": "frozen: x", "detail": json.dumps({"snap_age_s": 12})},
              {"whale": "rn1", "condition_id": "d", "us_market_slug": "s3", "his_long": 50.0,
               "snap_long": None, "would_side": None, "reason": "on target",
               "detail": json.dumps({"snap_stale": True})},
              {"whale": "rn1", "condition_id": "c", "us_market_slug": None, "reason": "unmapped"}]
    out = mr.summarize(latest, latest, {"rn1": {"ratio": 0.058}})
    assert out["rows"] == 4 and out["mapped_rows"] == 3 and out["unmapped_rows"] == 1
    assert out["would_orders"] == 2 and out["would_fill"] == 1 and out["would_fill_rate"] == 0.5
    assert out["frozen_rows"] == 1 and out["drift_n"] == 2 and out["drift_over_5pct"] == 1
    # drift is over the LARGER of the two readings: 10 / 110
    assert out["drift_p90"] == round(10 / 110, 4) and out["stale_snapshot_rows"] == 1
    # fills say he holds, the venue says he is out: full drift, counted
    gone = [{"whale": "rn1", "condition_id": "e", "us_market_slug": "s4", "his_long": 80.0,
             "snap_long": 0.0, "would_side": None, "reason": "on target", "detail": "{}"}]
    out2 = mr.summarize(gone, gone, {})
    assert out2["drift_n"] == 1 and out2["drift_p90"] == 1.0 and out2["drift_over_5pct"] == 1
    assert out["ratios"] == {"rn1": {"ratio": 0.058}}
    assert "NO ORDERS PLACED" in out["reading"]


def test_migration_046_and_the_endpoint_exist():
    sql = pathlib.Path(ms.__file__).parents[2].joinpath("migrations", "046_mirror_shadow.sql").read_text()
    for col in ("his_net", "snap_long", "target", "ledger_net", "venue_net", "would_fill", "reason"):
        assert col in sql
    app = pathlib.Path(ms.__file__).parents[1].joinpath("api", "app.py").read_text()
    assert '"/api/admin/mirror-shadow"' in app
    wf = pathlib.Path(ms.__file__).parents[3].joinpath(".github", "workflows", "engine-diagnostic.yml").read_text()
    assert "MIRRORREAD" in wf and "MIRRORHB" in wf and "/api/admin/mirror-shadow?hours=24" in wf


# ---------------------------------------------------------- review round two

def test_the_census_counts_judged_plans_and_an_unmapped_market_is_not_reread_every_tick(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    ms._unmapped_until.clear()

    class _P(_Pool):
        async def execute(self, sql, *a):
            await super().execute(sql, *a)
            return "UPDATE 1" if "/* judge-" in sql else None

    p = _P(fills=HIS, whales_ratio_fills=_ratio_fills())
    stats = _run(ms.tick_once(p, _Pmus(bid=0.30, ask=0.32), now_ts=5000.0))
    # three judge statements, each reporting one row: buy + sell + expire
    assert stats["resolved"] == 3 and stats["resolved_filled"] == 2
    # an unmapped market is remembered and skipped on the next tick, without a slot
    p2 = _Pool(fills=HIS, mapped=False, whales_ratio_fills=_ratio_fills())
    s1 = _run(ms.tick_once(p2, _Pmus(), now_ts=6000.0))
    assert s1["unmapped"] == 1 and s1["markets"] == 1
    s2 = _run(ms.tick_once(p2, _Pmus(), now_ts=6000.0 + 10))
    assert s2["markets"] == 0 and s2["skipped_unmapped"] == 1 and s2["rows"] == 0
    s3 = _run(ms.tick_once(p2, _Pmus(), now_ts=6000.0 + ms.UNMAPPED_TTL_S + 1))
    assert s3["markets"] == 1 and s3["unmapped"] == 1
    ms._unmapped_until.clear()


def test_ledger_net_signs_shorts_and_the_ledger_map_reads_a_short_row(monkeypatch):
    _nosleep(monkeypatch)
    rows = [{"sh": 100.0, "intent": "ORDER_INTENT_BUY_LONG"},
            {"sh": 40.0, "intent": "ORDER_INTENT_BUY_SHORT"},
            {"sh": 5.0, "intent": None}]
    p = _Pool(fills=HIS, ledger_rows=rows)
    assert _run(ms.ledger_net(p, SLUG)) == 65
    row = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {}, positions={SLUG: 65.0}))
    assert row["ledger_net"] == 65 and row["venue_net"] == 65.0
    assert not row["reason"].startswith("frozen") and row["would_side"] == "BUY_LONG"
    assert _run(ms.ledger_net(_Pool(ledger_rows=[{"sh": 30.0, "intent": "ORDER_INTENT_BUY_SHORT"}]),
                              SLUG)) == -30

    class _P(_Pool):
        async def fetch(self, sql, *a):
            s = " ".join(sql.split())
            if "FROM live_orders WHERE asset = ANY($1::text[])" in s:
                return [{"asset": M, "us_market_slug": SLUG, "intent": "ORDER_INTENT_BUY_SHORT"}]
            return await super().fetch(sql, *a)

    # a ledger row that SHORTED Michelsen names Nakashima as the long token
    m = _run(ms.map_market(_P(fills=HIS), HIS))
    assert m == {"us_slug": SLUG, "long_asset": N, "other_asset": M, "source": "ledger"}


def test_the_premap_fallback_names_the_long_token_from_either_side(monkeypatch):
    from sportsassets.workers import premap
    calls = []

    async def _short_side(pool, market_title, event_title, outcome, global_slug):
        calls.append((market_title, event_title, outcome, global_slug))
        if outcome == "Alex Michelsen":
            return {"market_slug": SLUG, "intent": "ORDER_INTENT_BUY_SHORT"}
        return None

    monkeypatch.setattr(premap, "resolve", _short_side)
    p = _Pool(fills=HIS, mapped=False)
    fills = [dict(f, event_title="US Open 2026") for f in HIS]
    m = _run(ms.map_market(p, fills))
    assert m == {"us_slug": SLUG, "long_asset": N, "other_asset": M, "source": "premap"}
    assert calls and calls[0][1] == "US Open 2026", "the event title reaches the resolver"

    async def _long_side(pool, market_title, event_title, outcome, global_slug):
        if outcome == "Brandon Nakashima":
            return {"market_slug": SLUG, "intent": "ORDER_INTENT_BUY_LONG"}
        return None

    monkeypatch.setattr(premap, "resolve", _long_side)
    m2 = _run(ms.map_market(p, fills))
    assert m2 == {"us_slug": SLUG, "long_asset": N, "other_asset": M, "source": "premap"}
    # the event title lives on the markets table, so his_fills joins it
    src = inspect.getsource(ms.his_fills)
    assert "LEFT JOIN markets m ON m.condition_id = t.condition_id" in src
    assert "m.event_title" in src and "t.event_title" not in src


def test_mirror_shadow_report_reads_newest_per_market_and_names_a_missing_table():
    import datetime as dt
    t0 = dt.datetime(2026, 9, 2, 18, 0, tzinfo=dt.timezone.utc)
    rows = [                                            # newest first, as the SQL orders
        {"at": t0, "whale": "rn1", "condition_id": CID, "us_market_slug": SLUG,
         "would_side": "BUY_LONG", "would_fill": None, "detail": "{}",
         "his_long": 10.0, "snap_long": 10.0},
        {"at": t0 - dt.timedelta(minutes=1), "whale": "rn1", "condition_id": CID,
         "us_market_slug": SLUG, "would_side": "BUY_LONG", "would_fill": True,
         "detail": '{"snap_stale": true}', "his_long": 10.0, "snap_long": None},
        {"at": t0 - dt.timedelta(minutes=2), "whale": "rn1", "condition_id": "0xother",
         "us_market_slug": None, "would_side": None, "would_fill": None, "detail": "{}"},
    ]

    class _P:
        def __init__(self, raise_fetch=False, ratio_raw=None):
            self.raise_fetch, self.ratio_raw, self.sql, self.args = raise_fetch, ratio_raw, None, None

        async def fetch(self, sql, *a):
            if self.raise_fetch:
                raise RuntimeError("no table")
            self.sql, self.args = " ".join(sql.split()), a
            return [dict(r) for r in rows]

        async def fetchval(self, sql, *a):
            return self.ratio_raw

    p = _P(ratio_raw=json.dumps({"rn1": {"ratio": 0.058}}))
    out = _run(mr.mirror_shadow_report(p, 6.0, "RN1"))
    assert "AND whale = $2" in p.sql and p.args == (6.0, "rn1")
    assert "($1::float8 * interval '1 hour')" in p.sql and "ORDER BY at DESC" in p.sql
    assert out["rows"] == 3 and out["hours"] == 6.0 and out["whale"] == "rn1"
    assert out["ratios"] == {"rn1": {"ratio": 0.058}}
    latest = {r["condition_id"]: r for r in out["latest"]}
    assert len(latest) == 2 and latest[CID]["at"] == t0.isoformat()
    assert latest[CID]["would_fill"] is None, "the newest row per market, not the oldest"
    assert out["would_orders"] == 2 and out["would_resolved"] == 1 and out["would_fill"] == 1
    assert out["would_fill_rate"] == 1.0 and out["stale_snapshot_rows"] == 1
    assert out["mapped_rows"] == 2 and out["unmapped_rows"] == 1
    # the ratio state may arrive decoded already
    p2 = _P(ratio_raw={"rn1": {"ratio": 0.058}})
    assert _run(mr.mirror_shadow_report(p2))["ratios"] == {"rn1": {"ratio": 0.058}}
    assert _run(mr.mirror_shadow_report(_P(raise_fetch=True))) == {
        "rows": 0, "error": "unavailable: RuntimeError", "latest": []}


def test_migration_046_columns_match_the_insert_and_the_report_select(monkeypatch):
    import re
    _nosleep(monkeypatch)
    sql = pathlib.Path(ms.__file__).parents[2].joinpath("migrations", "046_mirror_shadow.sql").read_text()
    body = re.search(r"CREATE TABLE IF NOT EXISTS mirror_shadow\s*\((.*?)\n\);", sql, re.S).group(1)
    cols = set()
    for ln in body.splitlines():
        t = ln.strip()
        if not t or t.startswith("--") or t.upper().startswith(("CONSTRAINT", "UNIQUE", "PRIMARY")):
            continue
        cols.add(t.split()[0])
    src = inspect.getsource(ms._write)
    ins = re.search(r"INSERT INTO mirror_shadow \((.*?)\)\s*VALUES", src, re.S).group(1)
    ins_cols = [c.strip() for c in ins.replace("\n", " ").split(",") if c.strip()]
    assert set(ins_cols) == cols - {"id", "at"}, (set(ins_cols) ^ (cols - {"id", "at"}))
    placeholders = re.findall(r"\$\d+", re.search(r"VALUES\s*\((.*?)\)\s*\"\"\"", src, re.S).group(1))
    assert len(placeholders) == len(ins_cols)
    # and the arguments actually passed match the placeholders
    p = _Pool(fills=HIS)
    _run(ms._write(p, {"whale": "rn1", "condition_id": CID, "detail": {}}))
    ins_call = [w for w in p.writes if "INSERT INTO mirror_shadow" in w[0]][0]
    assert len(ins_call[1]) == len(ins_cols)
    rep = " ".join(inspect.getsource(mr.mirror_shadow_report).split())
    sel = re.search(r"SELECT (.*?) FROM mirror_shadow", rep).group(1)
    sel_cols = {c.strip().split(" AS ")[0].split("::")[0].strip() for c in sel.split(",")}
    assert sel_cols <= cols, sel_cols - cols


def test_the_measurement_pacer_is_one_gate_for_both_workers(monkeypatch):
    from sportsassets import venue_pace as vp
    from sportsassets.workers import price_path as ppw
    assert ms.pace is vp.pace and ppw.pace is vp.pace, "one process-wide gate, not one per worker"
    assert "pace(READ_PACING_S)" in inspect.getsource(ms._paced_bbo)
    assert "pace(READ_PACING_S)" in inspect.getsource(ms.account_positions)
    assert "pace(READ_PACING_S)" in inspect.getsource(ppw._paced_ask)
    slept = []
    clock = {"t": 100.0}

    def _sleep(s):
        slept.append(round(s, 3))
        clock["t"] += s

    monkeypatch.setattr(vp.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(vp.time, "sleep", _sleep)
    monkeypatch.setattr(vp, "_last", 0.0)
    assert vp.pace(0.35) == 0.0                 # first read: no wait
    clock["t"] += 0.10
    assert round(vp.pace(0.35), 3) == 0.25      # 0.10 s later: wait the rest of the gap
    assert round(vp.pace(0.35), 3) == 0.35      # back-to-back: a full gap
    assert slept == [0.25, 0.35]


def test_the_long_side_is_chosen_by_shape_never_by_row_or_fill_order(monkeypatch):
    _nosleep(monkeypatch)
    # PER-SIDE IDENTIFIERS: each token resolves BUY_LONG on its own slug.
    # He holds 10,654.5 Michelsen and 367.42 Nakashima: the larger side
    # is the long, on ITS slug -- whichever row is newer
    for rows in ([{"asset": N, "us_market_slug": "slug-nak", "intent": "ORDER_INTENT_BUY_LONG"},
                  {"asset": M, "us_market_slug": "slug-mich", "intent": "ORDER_INTENT_BUY_LONG"}],
                 [{"asset": M, "us_market_slug": "slug-mich", "intent": "ORDER_INTENT_BUY_LONG"},
                  {"asset": N, "us_market_slug": "slug-nak", "intent": "ORDER_INTENT_BUY_LONG"}]):
        class _P(_Pool):
            def __init__(self, rows):
                super().__init__(fills=HIS)
                self.rows = rows

            async def fetch(self, sql, *a):
                if "FROM live_orders WHERE asset = ANY($1::text[])" in " ".join(sql.split()):
                    return list(self.rows)
                return await super().fetch(sql, *a)

        m = _run(ms.map_market(_P(rows), HIS))
        assert m == {"us_slug": "slug-mich", "long_asset": M, "other_asset": N,
                     "source": "ledger", "per_side": True}
    # the same through the premap fallback
    from sportsassets.workers import premap

    async def _both_long(pool, market_title, event_title, outcome, global_slug):
        return {"market_slug": "slug-mich" if outcome == "Alex Michelsen" else "slug-nak",
                "intent": "ORDER_INTENT_BUY_LONG"}

    monkeypatch.setattr(premap, "resolve", _both_long)
    # fills listed Nakashima-first: order must not decide
    fills = [HIS[-1]] + HIS[:-1]
    m2 = _run(ms.map_market(_Pool(fills=fills, mapped=False), fills))
    assert m2 == {"us_slug": "slug-mich", "long_asset": M, "other_asset": N,
                  "source": "premap", "per_side": True}
    # both long on ONE slug is ambiguous: refused, not guessed
    async def _same_slug(pool, market_title, event_title, outcome, global_slug):
        return {"market_slug": SLUG, "intent": "ORDER_INTENT_BUY_LONG"}

    monkeypatch.setattr(premap, "resolve", _same_slug)
    assert _run(ms.map_market(_Pool(fills=HIS, mapped=False), HIS)) is None
    # and the row says the market was read per side
    p = _Pool(fills=HIS, mapped=False)
    monkeypatch.setattr(premap, "resolve", _both_long)
    row = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {}, positions={}))
    assert row["us_market_slug"] == "slug-mich" and row["detail"]["per_side"] is True


def test_a_token_absent_from_a_fresh_complete_read_is_zero_and_from_a_partial_read_unknown(monkeypatch):
    _nosleep(monkeypatch)
    p = _Pool(fills=HIS)
    # complete read that no longer lists Michelsen: he merged out -> 0.0 (full drift)
    row = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {N: 367.42}, positions={},
                                snap_age_s=30.0, snap_partial=False))
    assert row["snap_long"] == 0.0 and row["snap_other"] == 367.42
    assert "snap_partial" not in row["detail"]
    # partial read: absent is unknown, named
    row2 = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {N: 367.42}, positions={},
                                 snap_age_s=30.0, snap_partial=True))
    assert row2["snap_long"] is None and row2["snap_other"] == 367.42
    assert row2["detail"]["snap_partial"] is True
    # stale: nothing, regardless
    row3 = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {N: 367.42}, positions={},
                                 snap_age_s=ms.SNAP_MAX_AGE_S + 1, snap_partial=False))
    assert row3["snap_long"] is None and row3["snap_other"] is None
    # snapshot_sizes carries the partial flag through; a missing key is partial
    p2 = _Pool(fills=HIS, snap={M: 1.0}, snap_at=ms.time.time() - 5)
    sizes, age, partial = _run(ms.snapshot_sizes(p2, "rn1"))
    assert sizes == {M: 1.0} and age is not None and age < 60 and partial is False
    assert _run(ms.snapshot_sizes(_Pool(), "rn1")) == ({}, None, True)


def test_a_positions_walk_that_hits_the_page_cap_is_unreadable_not_partial(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setattr(ms, "POSITIONS_PAGES_MAX", 2)
    # three pages, cap two: the third page's slug would read as "not held"
    pm = _Pmus(pages=[{"A": {"netPosition": 1}}, {"B": {"netPosition": 2}},
                      {"C": {"netPosition": 3}}])
    assert _run(ms.account_positions(pm)) is None
    assert pm.portfolio.calls == 2, "no read past the cap"
    # exactly at the cap with eof on the last page is a complete read
    pm2 = _Pmus(pages=[{"A": {"netPosition": 1}}, {"B": {"netPosition": 2}}])
    assert _run(ms.account_positions(pm2)) == {"a": 1.0, "b": 2.0}


def test_the_report_names_what_the_ledger_holds_on_a_frozen_slug():
    class _P:
        def __init__(self):
            self.sql = []

        async def fetch(self, sql, *a):
            self.sql.append((" ".join(sql.split()), a))
            return [{"id": 7, "status": "filled", "lane": "ioc", "whale_username": "rn1",
                     "sh": 604.0, "intent": "ORDER_INTENT_BUY_SHORT", "placed_at": "t"},
                    {"id": 3, "status": "settled", "lane": None, "whale_username": "manual",
                     "sh": 3458.0, "intent": None, "placed_at": "t0"}]

    latest = [{"us_market_slug": SLUG, "reason": "frozen: venue and ledger disagree",
               "venue_net": 3458.0, "ledger_net": -604},
              {"us_market_slug": "other", "reason": "on target"}]
    p = _P()
    out = _run(mr.frozen_detail(p, latest))
    assert len(out) == 1 and out[0]["slug"] == SLUG and out[0]["venue_net"] == 3458.0
    assert out[0]["rows"][0] == {"id": 7, "status": "filled", "lane": "ioc", "whale": "rn1",
                                 "sh": 604.0, "intent": "BUY_SHORT", "placed_at": "t"}
    assert out[0]["rows"][1]["intent"] is None and out[0]["rows"][1]["whale"] == "manual"
    assert "WHERE us_market_slug = $1" in p.sql[0][0] and p.sql[0][1] == (SLUG,)
    assert _run(mr.frozen_detail(p, [{"us_market_slug": "x", "reason": "on target"}])) == []


def test_a_frozen_market_is_counted_whatever_else_its_reason_says(monkeypatch):
    # the target's why comes first in the reason text; "frozen" may follow it
    rows = [{"us_market_slug": "s1", "reason": "short side not admitted; frozen: venue and ledger disagree",
             "his_long": 1.0, "snap_long": 1.0, "detail": "{}"},
            {"us_market_slug": "s2", "reason": "frozen: venue and ledger disagree", "detail": "{}"},
            {"us_market_slug": "s3", "reason": "on target", "detail": "{}"}]
    out = mr.summarize(rows, rows, {})
    assert out["frozen_rows"] == 2

    class _P:
        async def fetch(self, sql, *a):
            return []

    det = _run(mr.frozen_detail(_P(), rows))
    assert [d["slug"] for d in det] == ["s1", "s2"]
    # and the worker's census agrees
    _nosleep(monkeypatch)
    p = _Pool(fills=HIS + [_fill(N, "BUY", 20000, 0.46, 3300)],
              ledger_rows=[{"sh": 100.0, "intent": "ORDER_INTENT_BUY_LONG"}])
    row = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {}, positions={SLUG: 40.0}))
    assert "frozen" in row["reason"] and not row["reason"].startswith("frozen")


def test_a_fractional_ledger_against_a_whole_venue_position_is_not_frozen(monkeypatch):
    _nosleep(monkeypatch)
    p = _Pool(fills=HIS, ledger_rows=[{"sh": 322.51, "intent": "ORDER_INTENT_BUY_SHORT"}])
    row = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {}, positions={SLUG: -323.0}))
    assert row["ledger_net"] == -322 and row["venue_net"] == -323.0
    assert "frozen" not in row["reason"]


# ------------------------------------------ to-a-tee Phase 0: the instruments
# (owner order 2026-09-02 "mirror the whales to a tee"). Additive only:
# every fake, fixture and test above is unchanged and still pinned.

class _FactsPool(_Pool):
    """The base fake plus the slug's ledger rows (ledger_rows) and a
    switch that makes that read fail, so 'unreadable' is testable."""

    def __init__(self, *a, facts=None, facts_raise=False, **kw):
        super().__init__(*a, **kw)
        self.facts = facts or []
        self.facts_raise = facts_raise

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if "/* ledger-facts */" in s:
            self.queries.append((s, a))
            if self.facts_raise:
                raise RuntimeError("ledger unreadable")
            return list(self.facts)
        return await super().fetch(sql, *a)


def test_an_unmapped_row_names_the_market_in_its_detail(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setattr(ms.time, "time", lambda: 5000.0)
    from sportsassets.workers import premap
    calls = []

    async def _explain(pool, market_title, event_title, outcome, global_slug):
        calls.append((market_title, event_title, outcome, global_slug))
        return {"step": "no_key_intersection", "detail": "x", "keys": 5, "rows": 0}

    monkeypatch.setattr(premap, "resolve_explain", _explain)
    fills = [dict(f, event_title="US Open 2026", sport="tennis") for f in HIS]
    fills[-1] = dict(fills[-1], outcome=None)           # one chain row not yet enriched
    p = _Pool(fills=fills, mapped=False)
    row = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {}, positions={}))
    assert row["reason"].startswith("unmapped") and row.get("us_market_slug") is None
    d = row["detail"]
    assert d["his_slug"] == "atp-nakashi-michels-2026-09-02"
    assert d["title"] == "US Open ATP: Brandon Nakashima vs Alex Michelsen"
    assert d["event_title"] == "US Open 2026" and d["event_slug"] == "atp-nakashi-michels-2026-09-02"
    assert d["sport"] == "tennis" and d["family"] == "moneyline"
    assert d["explain"] == "no_key_intersection"
    assert calls == [("US Open ATP: Brandon Nakashima vs Alex Michelsen", "US Open 2026",
                      "Alex Michelsen", "atp-nakashi-michels-2026-09-02")]
    # his BUY dollars inside the lookback (the clock is pinned at 5000, so
    # every fixture fill is inside it), his gross shares, the NULL outcomes
    assert d["notional_6h"] == ms.notional_in_window(fills, ms.LOOKBACK_H, 5000.0) == 3534.88
    assert d["gross_sh"] == round(sum(ms.mi.net_positions(fills).values()), 4)
    assert d["outcome_null"] == 1
    # a resolver that raises is named, never guessed; sport falls back to the slug
    async def _boom(*a):
        raise RuntimeError("premap down")

    monkeypatch.setattr(premap, "resolve_explain", _boom)
    row2 = _run(ms.shadow_market(_Pool(fills=HIS, mapped=False), _Pmus(), "rn1", CID, RATIO, {},
                                 positions={}))
    assert row2["detail"]["explain"] == "explain_raised:RuntimeError"
    assert row2["detail"]["sport"] == "tennis"          # from the slug's league
    # the window arithmetic is pure
    assert ms.notional_in_window(HIS, 6.0, 1900.0 + 6 * 3600 + 1) == 0.0     # all older than 6 h
    assert ms.notional_in_window(HIS, 6.0, 1000.0 + 6 * 3600 + 1) == 2673.08  # the first fill aged out
    assert ms.notional_in_window(HIS, 6.0, 1900.0) == 3534.88
    assert ms.fills_since(HIS, 3500.0, 5000.0) == 1 and ms.fills_since(HIS, None) is None
    assert ms.outcome_null_count([{"outcome": ""}, {"outcome": "A"}, {}]) == 2


def test_a_mapped_row_carries_family_per_side_snapshot_state_and_ledger_facts(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setattr(ms.time, "time", lambda: 5000.0)
    facts = [{"status": "filled", "lane": "ioc", "error": None, "whale_username": "rn1"}]
    p = _FactsPool(fills=HIS, ledger_rows=[{"sh": 147.0, "intent": "ORDER_INTENT_BUY_LONG"}],
                   facts=facts)
    row = _run(ms.shadow_market(p, _Pmus(bid=0.30, ask=0.32), "rn1", CID, RATIO,
                                {M: 10654.5, N: 367.42}, positions={SLUG: 147.0},
                                snap_age_s=3500.0, snap_partial=True))
    d = row["detail"]
    assert d["map"] == "ledger" and d["family"] == "moneyline" and d["per_side"] is False
    # the ledger row on the slug is a per-fill (non-mirror) position: legacy,
    # and its class is what the row itself records
    assert d["ledger_legacy"] is True and d["map_class"] == "traded:ioc"
    assert p.queries[-1][1] == (SLUG,) or any(q[1] == (SLUG,) for q in p.queries if "/* ledger-facts */" in q[0])
    # snapshot: fresh (3500 s is stale) -> no: SNAP_MAX_AGE_S is 300, so this one is stale
    assert d["snap_state"] == "stale" and d["snap_stale"] is True
    assert d["fills_since_snap"] == 1                   # his fill at ts 1900 landed after 5000-3500
    # his gross dollars at the mark and the paired part a net mirror cannot hold
    assert d["his_gross_usd"] == round(10654.5 * 0.31 + 367.42 * (1 - 0.31), 2)
    assert d["his_paired_sh"] == 367.42 and d["his_sport"] == "tennis"
    # the three other snapshot states, by name
    row_fp = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {M: 1.0}, positions={SLUG: 147.0},
                                   snap_age_s=40.0, snap_partial=True))
    assert row_fp["detail"]["snap_state"] == "fresh_partial" and row_fp["detail"]["snap_partial"] is True
    row_fc = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {M: 1.0}, positions={SLUG: 147.0},
                                   snap_age_s=40.0, snap_partial=False))
    assert row_fc["detail"]["snap_state"] == "fresh_complete"
    assert "snap_partial" not in row_fc["detail"]          # the existing pin still holds
    row_none = _run(ms.shadow_market(p, _Pmus(), "rn1", CID, RATIO, {}, positions={SLUG: 147.0}))
    assert row_none["detail"]["snap_state"] == "none" and row_none["detail"]["fills_since_snap"] is None
    # an unreadable ledger read is named on both counts, never guessed
    p2 = _FactsPool(fills=HIS, facts_raise=True)
    row2 = _run(ms.shadow_market(p2, _Pmus(), "rn1", CID, RATIO, {}, positions={}))
    assert row2["detail"]["ledger_legacy"] is None and row2["detail"]["map_class"] == "unreadable"
    # a premap-sourced map carries its source as its class
    from sportsassets.workers import premap

    async def _long(pool, market_title, event_title, outcome, global_slug):
        return {"market_slug": SLUG, "intent": "ORDER_INTENT_BUY_LONG"} if outcome == "Alex Michelsen" else None

    monkeypatch.setattr(premap, "resolve", _long)
    row3 = _run(ms.shadow_market(_FactsPool(fills=HIS, mapped=False), _Pmus(), "rn1", CID, RATIO, {},
                                 positions={}))
    assert row3["detail"]["map"] == "premap" and row3["detail"]["map_class"] == "premap"
    assert row3["detail"]["ledger_legacy"] is False       # no rows on the slug
    # the census keys ride on the early returns too (no ratio, no mark)
    row4 = _run(ms.shadow_market(p, _Pmus(raise_bbo=True), "rn1", CID, RATIO, {}, positions={SLUG: 147.0}))
    assert row4["reason"].startswith("no mark") and row4["detail"]["family"] == "moneyline"
    assert row4["detail"]["ledger_legacy"] is True and "his_gross_usd" not in row4["detail"]


def test_ledger_facts_read_the_row_class_and_the_legacy_flag(monkeypatch):
    assert ms.ledger_facts(None) == {"legacy": None, "map_class": "unreadable"}
    assert ms.ledger_facts([]) == {"legacy": False, "map_class": "no_rows"}
    refused = {"status": "rejected", "lane": None, "whale_username": "rn1",
               "error": ("quarantined: mapping class unverified after wrong-side incident "
                         "2026-08-23 (src=fuzzy, slug=aec-atp-x-y-2026-09-02)")}
    assert ms.ledger_facts([refused]) == {"legacy": False, "map_class": "refused:fuzzy"}
    # the mirror's own book is not a legacy row
    assert ms.ledger_facts([{"status": "filled", "lane": "mirror", "error": None}]) == \
        {"legacy": False, "map_class": "traded:mirror"}
    # a per-fill row that traded is legacy; a recorded class beats 'traded'
    assert ms.ledger_facts([{"status": "filled", "lane": "ioc", "error": None}, refused]) == \
        {"legacy": True, "map_class": "refused:fuzzy"}
    assert ms.ledger_facts([{"status": "exiting", "lane": None, "error": None}]) == \
        {"legacy": True, "map_class": "traded:-"}
    assert ms.ledger_facts([{"status": "submitting", "lane": "ioc", "error": "x"}]) == \
        {"legacy": False, "map_class": "unrecorded"}
    # the read itself: by slug, every status that could explain a holding, newest first
    _nosleep(monkeypatch)
    p = _FactsPool(facts=[{"status": "filled", "lane": "ioc", "error": None}])
    assert _run(ms.ledger_rows(p, SLUG)) == [{"status": "filled", "lane": "ioc", "error": None}]
    q = [s for s, a in p.queries if "/* ledger-facts */" in s][0]
    assert "WHERE us_market_slug = $1" in q and "ORDER BY placed_at DESC" in q
    assert "'filled', 'exiting', 'settled', 'cashed_out', 'merged', 'submitting', 'open', 'rejected'" in q
    assert _run(ms.ledger_rows(_FactsPool(facts_raise=True), SLUG)) is None


def test_the_parallel_short_reading_leaves_the_live_compared_target_byte_identical(monkeypatch):
    _nosleep(monkeypatch)
    # his net is NEGATIVE: 20,000 Nakashima at 0.46 against 10,654 Michelsen
    fills = HIS + [_fill(N, "BUY", 20000, 0.46, 3300)]
    p = _Pool(fills=fills)
    row = _run(ms.shadow_market(p, _Pmus(bid=0.53, ask=0.55), "rn1", CID, RATIO, {}, positions={}))
    net = row["his_net"]
    assert net < 0
    # THE LIVE-COMPARED COLUMNS ARE EXACTLY THE LONG-ONLY READING
    tgt_long = ms.mi.target_shares(RATIO, net, row["mark"])
    assert row["target"] == tgt_long["target"] == 0 and row["target_raw"] == tgt_long["raw"]
    assert row["would_side"] is None and row["would_px"] is None and row["would_fill"] is None
    assert row["reason"] == "short side not admitted; on target"
    # ... and the short reading sits beside them, in the detail only
    d = row["detail"]
    tgt_s = ms.mi.target_shares(RATIO, net, row["mark"], allow_short=True)
    assert d["target_short"] == tgt_s["target"] < 0 and d["capped_short"] == tgt_s["capped"]
    assert d["target_raw_short"] == tgt_s["raw"]
    assert d["would_side_short"] == "SELL_LONG" and d["would_qty_short"] == -tgt_s["target"]
    # his equivalent is one minus what he paid for the other token (0.54);
    # the ask is 0.55, so the sell rests at 0.55 -- judged against the bid
    assert d["his_px_short"] == 0.54 and d["would_px_short"] == 0.55
    assert d["would_fill_short"] is None and d["reason_short"] == "reduce toward target"
    assert d["marketable_now_short"] is False               # bid 0.53 < 0.55
    # on his LONG side the two readings agree
    row2 = _run(ms.shadow_market(_Pool(fills=HIS), _Pmus(bid=0.30, ask=0.32), "rn1", CID, RATIO, {},
                                 positions={}))
    d2 = row2["detail"]
    assert d2["target_short"] == row2["target"] == 596
    assert d2["would_side_short"] == row2["would_side"] == "BUY_LONG"
    assert d2["would_px_short"] == row2["would_px"] == 0.30 and d2["would_qty_short"] == row2["would_qty"]
    # the source pins: the target is computed with the caller's allow_short
    # (never forced), and the INSERT knows nothing of the short reading
    src = inspect.getsource(ms.shadow_market)
    assert "mi.target_shares(ratio, net, mark, allow_short=allow_short)" in src
    assert "_short" not in inspect.getsource(ms._write)
    assert "allow_short=True" not in inspect.getsource(ms.tick_once)


def test_the_judge_records_the_touch_and_judges_the_short_reading_on_its_own_side(monkeypatch):
    _nosleep(monkeypatch)

    class _P(_Pool):
        def __init__(self, counts=None):
            super().__init__(fills=HIS)
            self.counts = counts or {}

        async def execute(self, sql, *a):
            await super().execute(sql, *a)
            for tag, n in self.counts.items():
                if tag in sql:
                    return f"UPDATE {n}"
            return "UPDATE 0"

    base = {"whale": "rn1", "condition_id": CID, "us_market_slug": SLUG, "detail": {}}
    p = _P(counts={"/* judge-buy */": 1, "/* judge-short-sell */": 2, "/* judge-short-expire */": 1})
    census: dict = {}
    pm = _Pmus(bid=0.29, ask=0.30)
    res, fil = _run(ms._write(p, dict(base, bid=0.29, ask=0.30), census, pm))
    # the long reading's counts are what they were; the short reading's are separate
    assert (res, fil) == (1, 1)
    assert census["resolved_short"] == 3 and census["resolved_filled_short"] == 2
    buy = [w for w in p.writes if "/* judge-buy */" in w[0]][0]
    assert ("jsonb_build_object('touched_s', round(extract(epoch FROM (now() - at)))::int, "
            "'touch_px', $3::float8)") in buy[0]
    assert buy[1] == ("rn1", CID, 0.30, ms.JUDGE_TTL_S)        # the argument tuple is unchanged
    exp = [w for w in p.writes if "/* judge-expire */" in w[0]][0]
    assert "'expired_s'" in exp[0] and exp[1] == ("rn1", CID, ms.JUDGE_TTL_S)
    # the short SELL fills when the bid comes UP to its price ...
    ss = [w for w in p.writes if "/* judge-short-sell */" in w[0]][0]
    assert "detail->>'would_side_short' = 'SELL_LONG'" in ss[0]
    assert "(detail->>'would_px_short')::float8 <= $3" in ss[0] and "'would_fill_short', true" in ss[0]
    assert "detail->>'would_fill_short' IS NULL" in ss[0]
    assert ss[1] == ("rn1", CID, 0.29, ms.JUDGE_TTL_S)
    # ... its BUY (a short reduce) when the ask comes down ...
    sb = [w for w in p.writes if "/* judge-short-buy */" in w[0]][0]
    assert "detail->>'would_side_short' = 'BUY_LONG'" in sb[0]
    assert "(detail->>'would_px_short')::float8 >= $3" in sb[0]
    assert sb[1] == ("rn1", CID, 0.30, ms.JUDGE_TTL_S)
    # ... and it expires past the same TTL
    se = [w for w in p.writes if "/* judge-short-expire */" in w[0]][0]
    assert "'would_fill_short', false" in se[0]
    assert "at < now() - ($3::float8 * interval '1 second')" in se[0]
    assert se[1] == ("rn1", CID, ms.JUDGE_TTL_S)
    # the short judge never writes the live-compared column
    for w in (ss, sb, se):
        assert "would_fill =" not in w[0] and "SET would_fill" not in w[0]
    # something was touched this tick: ONE paced depth read, stamped on the
    # touched rows that carry no depth yet; the fake client has no book, so
    # the reading is null -- named, never guessed
    dep = [w for w in p.writes if "/* judge-depth */" in w[0]]
    assert len(dep) == 1 and dep[0][1][:2] == ("rn1", CID)
    assert "NOT (detail ? 'touch_depth')" in dep[0][0]
    assert "(detail ? 'touched_s' OR detail ? 'touched_s_short')" in dep[0][0]
    assert json.loads(dep[0][1][2]) is None
    assert census["touch_depth_reads"] == 1
    # nothing touched: no depth read, no depth statement
    p2 = _P(counts={"/* judge-expire */": 1})
    census2: dict = {}
    _run(ms._write(p2, dict(base, bid=0.29, ask=0.30), census2, pm))
    assert not [w for w in p2.writes if "/* judge-depth */" in w[0]]
    assert census2.get("touch_depth_reads", 0) == 0
    # an unreadable book judges nothing on either side; a one-sided book
    # judges only the side that can reach us
    p3 = _P()
    _run(ms._write(p3, dict(base, bid=None, ask=None), {}, pm))
    assert not [w for w in p3.writes if "/* judge-" in w[0]]
    p4 = _P()
    _run(ms._write(p4, dict(base, bid=None, ask=0.30), {}, pm))
    assert [w for w in p4.writes if "/* judge-short-buy */" in w[0]]
    assert not [w for w in p4.writes if "/* judge-short-sell */" in w[0]]
    # without a venue handle the judge still runs; no depth is read
    p5 = _P(counts={"/* judge-sell */": 1})
    assert _run(ms._write(p5, dict(base, bid=0.29, ask=0.30))) == (1, 1)
    assert json.loads([w for w in p5.writes if "/* judge-depth */" in w[0]][0][1][2]) is None


def test_book_depth_reads_the_best_level_of_each_side_and_fails_closed(monkeypatch):
    class _Markets:
        def __init__(self, raw, raise_=False):
            self.raw, self.raise_, self.calls = raw, raise_, []

        def book(self, slug):
            self.calls.append(slug)
            if self.raise_:
                raise RuntimeError("429")
            return self.raw

    class _C:
        def __init__(self, raw, raise_=False):
            self.markets = _Markets(raw, raise_)

    raw = {"marketData": {"bids": [{"px": {"value": "0.30"}, "qty": "120"},
                                   {"px": {"value": "0.29"}, "qty": "500"}],
                          "offers": [{"px": {"value": "0.33"}, "qty": "40"},
                                     {"px": {"value": "0.32"}, "qty": "75"}]}}
    assert ms._book_depth(_C(raw), SLUG) == {"bid": 0.30, "bid_qty": 120.0, "ask": 0.32, "ask_qty": 75.0}
    assert ms._book_depth(_C({"book": {"asks": [{"px": 0.4, "qty": 3}]}}), SLUG) == {"ask": 0.4, "ask_qty": 3.0}
    assert ms._book_depth(_C({"marketData": {}}), SLUG) is None
    assert ms._book_depth(_C({"marketData": {"bids": [{"px": {"value": "bad"}, "qty": 1}]}}), SLUG) is None
    assert ms._book_depth(_C(None, raise_=True), SLUG) is None
    # the paced read goes through the one measurement gate, and a client
    # without a book surface reads None
    slept = _nosleep(monkeypatch)
    assert ms._paced_depth(_Pmus(), SLUG) is None and ms.READ_PACING_S in slept
    assert "pace(READ_PACING_S)" in inspect.getsource(ms._paced_depth)


def test_tick_once_carries_the_short_census_beside_the_long_one(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    ms._unmapped_until.clear()

    class _P(_Pool):
        async def execute(self, sql, *a):
            await super().execute(sql, *a)
            return "UPDATE 1" if "/* judge-" in sql else None

    p = _P(fills=HIS, whales_ratio_fills=_ratio_fills())
    stats = _run(ms.tick_once(p, _Pmus(bid=0.30, ask=0.32), now_ts=5000.0))
    # the long census is what it was ...
    assert stats["resolved"] == 3 and stats["resolved_filled"] == 2 and stats["would_orders"] == 1
    # ... and the short reading's rides beside it
    assert stats["would_orders_short"] == 1
    assert stats["resolved_short"] == 3 and stats["resolved_filled_short"] == 2
    assert stats["touch_depth_reads"] == 1
    ms._unmapped_until.clear()


# ------------------------------------------ Phase 0 review of the instruments
# (owner order 2026-09-02 "mirror the whales to a tee"): the ledger-facts read
# and the module-level pattern. Additive only.

def _sqlite_live_orders(rows):
    """A live_orders table in memory with the columns the ledger-facts read
    touches, so the read's own SQL can be executed rather than pattern-
    matched: the review's failure is a truncation, and only running the
    query shows which rows survive it."""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE live_orders (id INTEGER PRIMARY KEY, us_market_slug TEXT, status TEXT, "
                "lane TEXT, error TEXT, whale_username TEXT, placed_at REAL)")
    con.executemany("INSERT INTO live_orders (id, us_market_slug, status, lane, error, whale_username, placed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return con


def _run_sqlite(con, sql, slug):
    # asyncpg's $1 is sqlite's ?1 (the same parameter, bound wherever it appears)
    cur = con.execute(sql.replace("$1", "?1"), (slug,))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def test_the_live_rows_never_fall_off_the_ledger_facts_read():
    # a per-fill ioc row FILLED on this slug, then twenty-one newer rows the
    # quarantine rejected as he traded the other token: the review's case
    quarantined = ("quarantined: mapping class unverified after wrong-side incident "
                   "2026-08-23 (src=fuzzy, slug=" + SLUG + ")")
    rows = [(1, SLUG, "filled", "ioc", None, "rn1", 1000.0)]
    rows += [(i, SLUG, "rejected", None, quarantined, "rn1", 1000.0 + i) for i in range(2, 23)]
    rows += [(99, "aec-other-slug-2026-09-02", "filled", "ioc", None, "rn1", 5000.0)]
    con = _sqlite_live_orders(rows)
    got = _run_sqlite(con, ms._SQL_LEDGER_FACTS, SLUG)
    # the filled row is read whole, the rejected rows under the newest-20 cap
    assert len(got) == 21 and got[-1]["status"] == "filled" and got[-1]["lane"] == "ioc"
    assert sum(1 for r in got if r["status"] == "rejected") == 20
    assert all(r["whale_username"] == "rn1" for r in got)
    facts = ms.ledger_facts(got)
    assert facts["legacy"] is True and facts["map_class"] == "refused:fuzzy"
    # the shape the review named: a plain newest-20 read drops the filled row
    # and reads a confident False -- the pin above is what stops that
    naive = _run_sqlite(con, """
        SELECT status, lane, error, whale_username FROM live_orders
         WHERE us_market_slug = $1
           AND status IN ('filled', 'exiting', 'settled', 'cashed_out', 'merged',
                          'submitting', 'open', 'rejected')
         ORDER BY placed_at DESC LIMIT 20""", SLUG)
    assert ms.ledger_facts(naive)["legacy"] is False
    # an exiting row outside the cap is live too; a slug with no live row
    # under thirty rejected rows still reads False, and the class comes from
    # the newest rows the cap keeps
    con2 = _sqlite_live_orders(
        [(1, SLUG, "exiting", None, None, "rn1", 1.0)]
        + [(i, SLUG, "rejected", None, quarantined, "rn1", float(i)) for i in range(2, 32)])
    assert ms.ledger_facts(_run_sqlite(con2, ms._SQL_LEDGER_FACTS, SLUG)) == \
        {"legacy": True, "map_class": "refused:fuzzy"}
    con3 = _sqlite_live_orders(
        [(i, SLUG, "rejected", None, quarantined, "rn1", float(i)) for i in range(1, 31)])
    got3 = _run_sqlite(con3, ms._SQL_LEDGER_FACTS, SLUG)
    assert len(got3) == 20 and ms.ledger_facts(got3) == {"legacy": False, "map_class": "refused:fuzzy"}
    # the worker's read is that SQL verbatim, and the existing pins on it hold
    p = _FactsPool(facts=[{"status": "filled", "lane": "ioc", "error": None}])
    assert _run(ms.ledger_rows(p, SLUG)) == [{"status": "filled", "lane": "ioc", "error": None}]
    q = [s for s, a in p.queries if "/* ledger-facts */" in s][0]
    assert q == " ".join(ms._SQL_LEDGER_FACTS.split())
    assert "status IN ('filled', 'exiting') OR id IN (SELECT id FROM live_orders" in q
    assert q.count("LIMIT") == 1 and q.endswith("ORDER BY placed_at DESC /* ledger-facts */")


def test_the_src_pattern_is_compiled_with_the_module():
    import re as _re
    assert isinstance(ms._SRC_RE, _re.Pattern) and ms._SRC_RE.pattern == r"\(src=([a-z_]+),"
    src = inspect.getsource(ms.ledger_facts)
    assert "global " not in src and "import re" not in src and "_SRC_RE is None" not in src
    head = pathlib.Path(ms.__file__).read_text().split("\ndef ", 1)[0]
    assert "\nimport re\n" in head and "_SRC_RE = re.compile(" in pathlib.Path(ms.__file__).read_text()


def test_an_unreadable_position_row_makes_the_whole_walk_unreadable(monkeypatch):
    """A row we cannot parse used to be skipped while the walk still
    reported itself COMPLETE. The caller then reads venue 0 for that
    slug, the "the venue already holds this" admission clause passes,
    and a BUY leaves into a market the account already holds. The page
    cap already refuses a truncated walk for exactly this reason; an
    unreadable row is the same defect one row at a time."""
    _nosleep(monkeypatch)

    # an unparseable netPosition
    pm = _Pmus(pages=[{"A": {"netPosition": 1}, "B": {"netPosition": "not-a-number"}}])
    assert _run(ms.account_positions(pm)) is None, "one bad row, no reading"

    # a row whose slug is missing or blank
    for bad in ("", "   ", None):
        pm2 = _Pmus(pages=[{"A": {"netPosition": 1}, bad: {"netPosition": 2}}])
        assert _run(ms.account_positions(pm2)) is None, f"slug {bad!r} is not a name"

    # the clean walk is untouched, including a null netPosition (venue's zero)
    pm3 = _Pmus(pages=[{"A": {"netPosition": 1}, "B": {"netPosition": None}, "C": {}}])
    assert _run(ms.account_positions(pm3)) == {"a": 1.0, "b": 0.0, "c": 0.0}


def test_a_non_finite_or_duplicate_position_row_is_also_unreadable(monkeypatch):
    """Round-one review, folded: a NaN passes float() but wedges any book
    on that slug (int(nan) raises every tick, so the book can never be
    planned, frozen by name, or flattened), and a duplicate normalised key
    is the walk's own defect by another route -- last-write-wins can
    report 0 for a slug that IS held."""
    _nosleep(monkeypatch)
    for bad in ("nan", "inf", "-inf", float("nan")):
        pm = _Pmus(pages=[{"A": {"netPosition": 1}, "B": {"netPosition": bad}}])
        assert _run(ms.account_positions(pm)) is None, f"{bad!r} is not a reading"
    # "AB" and "ab " normalise to the same name: which one is the truth?
    pm2 = _Pmus(pages=[{"AB": {"netPosition": 1}, "ab ": {"netPosition": 0}}])
    assert _run(ms.account_positions(pm2)) is None
    # a non-string slug can never match us_market_slug, so it is no name
    for bad_slug in (5, True):
        pm3 = _Pmus(pages=[{"A": {"netPosition": 1}, bad_slug: {"netPosition": 2}}])
        assert _run(ms.account_positions(pm3)) is None
