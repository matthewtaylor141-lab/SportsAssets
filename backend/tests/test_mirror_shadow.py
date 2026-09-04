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
import shutil
import subprocess

import pytest

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


def test_no_shadow_knob_can_be_loosened_from_a_shell(monkeypatch, request):
    """Every dollar cap in the rules has been downward-only since they
    were written; these were raw env reads. One of them is a money bound
    in disguise: SNAP_MAX_AGE_S is the freshness gate on HIS position --
    the live worker turns it into the admission fact that the drift
    rule's increase clause and the snapshot resolution key on -- so a
    shell could otherwise open new books and grow live ones on an
    arbitrarily old reading of the whale, and loosen the very gate the
    rollout is steered by, with no deploy and no review."""
    import importlib
    # whatever this test does to the module, the next test gets the
    # module the ambient environment actually describes
    request.addfinalizer(lambda: importlib.reload(ms))
    knobs = {
        "MIRROR_SNAP_MAX_AGE_S": ("SNAP_MAX_AGE_S", 300.0),
        "MIRROR_JUDGE_TTL_S": ("JUDGE_TTL_S", 600.0),
        "MIRROR_LOOKBACK_H": ("LOOKBACK_H", 6.0),
        "MIRROR_RATIO_DAYS": ("RATIO_DAYS", 30),
        "MIRROR_MAX_MARKETS": ("MAX_MARKETS_PER_TICK", 20),
        # the exit leg's own two bounds (A3): a longer window and a
        # bigger market cap are both MORE reading, so both tighten only
        "MIRROR_EXIT_WINDOW_H": ("EXIT_WINDOW_H", 24.0),
        "MIRROR_EXIT_SUMMARY_MAX": ("EXIT_SUMMARY_MAX", 2000),
    }
    for env, (attr, default) in knobs.items():
        for huge in ("999999", "1e9", "inf"):
            monkeypatch.setenv(env, huge)
            mod = importlib.reload(ms)
            assert getattr(mod, attr) == default, f"{env}={huge} loosened {attr}"
            monkeypatch.delenv(env, raising=False)
        # a garbage value is the code default, not a crash and not zero
        monkeypatch.setenv(env, "not-a-number")
        assert getattr(importlib.reload(ms), attr) == default
        monkeypatch.delenv(env, raising=False)
    # tightening still works without a deploy
    monkeypatch.setenv("MIRROR_MAX_MARKETS", "3")
    monkeypatch.setenv("MIRROR_JUDGE_TTL_S", "60")
    mod = importlib.reload(ms)
    assert mod.MAX_MARKETS_PER_TICK == 3 and mod.JUDGE_TTL_S == 60.0
    monkeypatch.delenv("MIRROR_MAX_MARKETS", raising=False)
    monkeypatch.delenv("MIRROR_JUDGE_TTL_S", raising=False)


def test_the_two_sided_knobs_have_a_floor_that_means_something(monkeypatch, request):
    """Not every knob is safe in both directions, and two here are not.

    SNAP_MAX_AGE_S judges HIS position stale or fresh. Raised, new books
    open on an old reading; LOWERED, a book whose snapshot reads stale
    takes select_flatten's vanished path -- the only path that accepts
    slippage -- so a short window deletes the paired-flatten guard for
    every book. Its floor is the snapshot WRITER's own cadence, under
    which every read is stale by construction.

    POLL_S is an interval, so its aggressive direction is DOWN (more
    venue reads on a key the live lane shares). It lengthens only, which
    also keeps the operator's incident lever."""
    import importlib
    request.addfinalizer(lambda: importlib.reload(ms))
    monkeypatch.setenv("MIRROR_SNAP_MAX_AGE_S", "1")
    mod = importlib.reload(ms)
    assert mod.SNAP_MAX_AGE_S == 120.0, "a window under the writer's cadence is always stale"
    monkeypatch.delenv("MIRROR_SNAP_MAX_AGE_S", raising=False)
    # the interval slows down from a shell but never speeds up
    monkeypatch.setenv("MIRROR_SHADOW_POLL_S", "1")
    assert importlib.reload(ms).POLL_S == 30.0
    monkeypatch.setenv("MIRROR_SHADOW_POLL_S", "300")
    assert importlib.reload(ms).POLL_S == 300.0, "the operator can still slow the shadow"
    monkeypatch.delenv("MIRROR_SHADOW_POLL_S", raising=False)
    # the exit census's interval is the same shape: its aggressive
    # direction is DOWN (more database reads on the shadow's own table)
    monkeypatch.setenv("MIRROR_EXIT_SUMMARY_S", "1")
    assert importlib.reload(ms).EXIT_SUMMARY_S == 900.0
    monkeypatch.setenv("MIRROR_EXIT_SUMMARY_S", "3600")
    assert importlib.reload(ms).EXIT_SUMMARY_S == 3600.0, "the operator can still slow it"
    monkeypatch.delenv("MIRROR_EXIT_SUMMARY_S", raising=False)
    # and the exit window's floor is an hour: a window under it could
    # not carry the 24 h reading the gate is defined over
    monkeypatch.setenv("MIRROR_EXIT_WINDOW_H", "0.1")
    assert importlib.reload(ms).EXIT_WINDOW_H == 1.0
    monkeypatch.delenv("MIRROR_EXIT_WINDOW_H", raising=False)
    # THE MARKET CAP'S FLOOR IS KEPT OFF THE GATE'S MINIMUM n. At
    # floor=30 a shell could pin the census to exactly the n the gate
    # reads at; every day busier than that then refuses (the truncation
    # rule below), so the floor is what keeps the instrument usable at
    # all. It sits far above both the minimum n and the mapped-market
    # count the mirror programme records for a 24 h window.
    for tight in ("5", "30", "499"):
        monkeypatch.setenv("MIRROR_EXIT_SUMMARY_MAX", tight)
        mod = importlib.reload(ms)
        assert mod.EXIT_SUMMARY_MAX == 500, tight
        assert mod.EXIT_SUMMARY_MAX > mod.EXIT_MIN_N
    # and tightening within the range still works without a deploy
    monkeypatch.setenv("MIRROR_EXIT_SUMMARY_MAX", "800")
    assert importlib.reload(ms).EXIT_SUMMARY_MAX == 800
    monkeypatch.delenv("MIRROR_EXIT_SUMMARY_MAX", raising=False)


def test_the_snapshot_writers_own_cadence_cannot_be_stretched(monkeypatch, request):
    """The freshness gate has two ends. The mirror reads an age; this
    worker writes the snapshot that age is measured from, so a long
    interval makes every snapshot stale without touching the mirror's
    knob at all -- the same loosening from the far side."""
    import importlib
    from sportsassets.workers import whale_exits as wx
    request.addfinalizer(lambda: importlib.reload(wx))
    for huge in ("100000", "1e9", "inf"):
        monkeypatch.setenv("WHALE_EXIT_INTERVAL_S", huge)
        assert importlib.reload(wx).INTERVAL_S == 120.0, huge
    monkeypatch.setenv("WHALE_EXIT_INTERVAL_S", "30")
    assert importlib.reload(wx).INTERVAL_S == 30.0, "a faster writer is still allowed"
    monkeypatch.delenv("WHALE_EXIT_INTERVAL_S", raising=False)


# ------------------------------------------------ A3: MIRROREXIT, the exit leg
#
# The shadow judged ENTRIES and recorded nothing about exits. These pin
# the other half: that he reduced or LEFT, when, at what price the
# COMPLEMENT traded (he exits by buying it, not by selling), what our
# own leg was, what the exit rule would have done, whether that plan
# would have filled inside the same TTL against the book we read, and
# the reason when there is no plan. Nothing here places, judges or
# re-plans anything: the exit leg names the plan the row already
# carries, and its verdict is that row's own would_fill.

NOW = 1_788_000_000.0
EXIT_SLUG = SLUG


def _hisfill(asset, side, size, price, ts_offset, i=0):
    return _fill(asset, side, size, price, NOW + ts_offset, id=i)


# he opened 1,000 Michelsen an hour ago and completed 400 of the pair
# two minutes ago at 0.68 -- his exit is a BUY, and only his net says so
EXITFILLS = [_hisfill(M, "BUY", 1000, 0.30, -3600, 1),
             _hisfill(N, "BUY", 400, 0.68, -120, 2)]


def _at_now(monkeypatch):
    monkeypatch.setattr(ms.time, "time", lambda: NOW)


def _reset_exit_cache():
    ms._exit_cache.update(at=0.0, value=None)


@pytest.fixture(autouse=True)
def _module_globals_are_not_load_bearing():
    """`_exit_cache` and `_backoff_until` are MODULE GLOBALS that outlive
    a test: several tests here reset them by hand and one leaves the
    cache populated, so without a finalizer any later test that calls
    `tick_once` inherits whichever of these ran before it. This is the
    hazard R2's own review folded a reload finalizer for; the same rule,
    applied to the two globals this unit added. Both ends, so an
    ordering change cannot turn a green suite into a lie."""
    _reset_exit_cache()
    ms._backoff_until = 0.0
    yield
    _reset_exit_cache()
    ms._backoff_until = 0.0


class _ExitPool(_Pool):
    """A pool that also answers the exit-leg census read."""

    def __init__(self, *a, census=None, census_raise=False, census_hangs=False, **kw):
        super().__init__(*a, **kw)
        self.census, self.census_raise, self.census_hangs = census or [], census_raise, census_hangs
        self.census_reads = 0

    async def fetch(self, sql, *a):
        if "/* exit-leg-census */" in " ".join(sql.split()):
            self.census_reads += 1
            self.queries.append((" ".join(sql.split()), a))
            if self.census_raise:
                raise RuntimeError('relation "mirror_shadow" does not exist')
            if self.census_hangs:
                await asyncio.sleep(30)
            return list(self.census)
        return await super().fetch(sql, *a)


def test_the_exit_leg_records_his_reduction_and_what_the_rule_would_have_done(monkeypatch):
    """One reading, per whale and per market: he reduced, when, the
    complement's own price, our leg by both readings, and the plan the
    exit rule would have rested -- at HIS equivalent, not at the book."""
    _nosleep(monkeypatch)
    _at_now(monkeypatch)
    p = _Pool(fills=EXITFILLS, ledger_rows=[{"sh": 100.0, "intent": "ORDER_INTENT_BUY_LONG"}])
    pm = _Pmus(bid=0.26, ask=0.29)
    row = _run(ms.shadow_market(p, pm, "rn1", CID, RATIO, {}, positions={EXIT_SLUG: 100.0}))
    d = row["detail"]
    # THAT HE REDUCED, AND WHEN
    assert d["exit_kind"] == "reduced" and d["exit_move"] == "bought_complement"
    assert d["exit_at"] == NOW - 120 and d["exit_age_s"] == 120.0
    assert d["exit_his_net_before"] == 1000.0 and d["exit_his_net_after"] == 600.0
    # AT WHAT PRICE THE COMPLEMENT TRADED, and what that is in long terms
    assert d["exit_complement_px"] == 0.68 and d["exit_his_px"] == 0.32
    assert d["exit_size"] == 400.0
    # WHAT OUR OWN LEG WAS -- both readings the plan is fail-closed on
    assert d["exit_ledger"] == 100.0 and d["exit_venue"] == 100.0 and d["exit_target"] == 34
    # WHAT THE EXIT RULE WOULD HAVE DONE: rest at his equivalent (0.32),
    # above the ask (0.29) -- a maker rest, never a cross
    assert d["exit_plan"] == "rest" and d["exit_side"] == "SELL_LONG"
    assert d["exit_qty"] == 66 and d["exit_px"] == 0.32
    assert d["exit_at_his_level"] is True and d["exit_marketable_now"] is False
    # ... and it is the row's OWN plan, named -- not a second arithmetic
    assert (row["would_side"], row["would_qty"], row["would_px"]) == ("SELL_LONG", 66, 0.32)
    # the exit leg costs no venue read at all: one BBO for the market
    assert pm.calls == [("bbo", EXIT_SLUG)]


def test_the_exit_leg_reads_an_exit_as_a_buy_of_the_complement_and_expires_with_the_window():
    """`he never sells` is true of the wire and false of the position:
    a rule keyed on his SELLs sees almost nothing of him (decision 18
    in the to-a-tee programme records how few sells he has ever made),
    a rule keyed on his NET sees every exit. And an exit is only
    current while
    it is the last thing he did: an add behind it, or an age past the
    window the shadow reads, is not an exit now."""
    ev = ms.reduction_event(EXITFILLS, M, N, now_ts=NOW)
    assert ev["move"] == "bought_complement" and ev["kind"] == "reduced"
    assert not [f for f in EXITFILLS if f["side"] == "SELL"], "he sold nothing at all"
    # completing the whole pair is LEFT, not reduced
    left = ms.reduction_event(EXITFILLS[:1] + [_hisfill(N, "BUY", 1000, 0.68, -60, 3)],
                              M, N, now_ts=NOW)
    assert left["kind"] == "left" and left["net_after"] == 0.0 and left["left"] is True
    # the other shape still reads: a plain SELL of the long leg
    sold = ms.reduction_event(EXITFILLS[:1] + [_hisfill(M, "SELL", 400, 0.31, -60, 3)],
                              M, N, now_ts=NOW)
    assert sold["move"] == "sold_long" and sold["px_equiv"] == 0.31
    assert sold["complement_px"] is None and sold["net_after"] == 600.0
    # HE IS ADDING AGAIN: the reduction behind it is history, not an exit
    assert ms.reduction_event(EXITFILLS + [_hisfill(M, "BUY", 50, 0.33, -30, 3)],
                              M, N, now_ts=NOW) is None
    # an ancient reduction on a market he has just re-entered is not one
    old = [_hisfill(M, "BUY", 1000, 0.30, -100_000, 1),
           _hisfill(N, "BUY", 400, 0.68, -90_000, 2)]
    assert ms.reduction_event(old, M, N, now_ts=NOW) is None
    assert ms._exit_max_age_s() == ms.LOOKBACK_H * 3600.0
    # an unreadable price is a reduction we cannot price, not an absent one
    bad = EXITFILLS[:1] + [_hisfill(N, "BUY", 400, None, -60, 3)]
    ev_bad = ms.reduction_event(bad, M, N, now_ts=NOW)
    assert ev_bad["px_equiv"] is None and ev_bad["complement_px"] is None
    assert ev_bad["kind"] == "reduced"
    # HE NEVER HELD THE LONG SIDE: buying the complement from flat opens a
    # short of it, which is the short reading's question, not an exit
    assert ms.reduction_event([_hisfill(N, "BUY", 400, 0.68, -60, 1)], M, N, now_ts=NOW) is None
    # and a market whose long leg we could not name makes no exit claim
    assert ms.reduction_event(EXITFILLS, None, N, now_ts=NOW) is None
    # two fills on ONE timestamp keep the query's order (ts, id), never a
    # lexicographic one: "10" must not sort before "9"
    same = [_hisfill(M, "BUY", 1000, 0.30, -600, 9),
            _hisfill(N, "BUY", 100, 0.60, -60, 9),
            _hisfill(M, "BUY", 5, 0.31, -60, 10)]
    assert ms.reduction_event(same, M, N, now_ts=NOW) is None, "the last fill on the tick is his add"


def test_exit_leg_judges_the_sell_side_over_the_same_ttl(monkeypatch):
    """The verdict on an exit rest is the row's own would_fill: judged
    on the SELL side -- the bid came UP to our price -- inside the same
    JUDGE_TTL_S the BUY judge uses. There is ONE judge and one verdict;
    a second one could disagree with the first."""
    _nosleep(monkeypatch)
    _at_now(monkeypatch)
    src = inspect.getsource(ms._resolve_previous)
    assert "/* judge-sell */" in src and "would_side = 'SELL_LONG'" in src
    assert "would_px <= $3" in src and "($4::float8 * interval '1 second')" in src
    # the exit leg writes no verdict of its own and no second judge
    assert "exit_fill" not in inspect.getsource(ms)
    assert "judge-exit" not in inspect.getsource(ms)
    assert "would_fill" in ms._SQL_EXIT_CENSUS, "the census reads the row's own verdict"
    # and the row it labels is exactly the row that judge resolves
    p = _Pool(fills=EXITFILLS, ledger_rows=[{"sh": 100.0, "intent": "ORDER_INTENT_BUY_LONG"}])
    row = _run(ms.shadow_market(p, _Pmus(bid=0.26, ask=0.29), "rn1", CID, RATIO, {},
                                positions={EXIT_SLUG: 100.0}))
    assert row["detail"]["exit_px"] == row["would_px"] and row["would_side"] == "SELL_LONG"
    assert row["would_fill"] is None, "unobserved is not unfilled"
    # a bid that reached 0.32 inside the TTL fills it; the same reading
    # past the TTL does not. Both are the SELL judge's own two writes.
    counts = {"/* judge-sell */": 1}

    class _P(_Pool):
        async def execute(self, sql, *a):
            await super().execute(sql, *a)
            return f"UPDATE {counts.get(next((t for t in counts if t in sql), ''), 0)}"

    q = _P(fills=EXITFILLS)
    assert _run(ms._write(q, dict(row))) == (1, 1)
    sell = [w for w in q.writes if "/* judge-sell */" in w[0]][0]
    assert sell[1] == ("rn1", CID, 0.26, ms.JUDGE_TTL_S)


def test_exit_leg_clusters_by_market_not_by_plan():
    """§3b reads this gate MARKET-clustered over >= 30 markets. A market
    read two hundred times is one market's worth of evidence: the census
    returns ONE row per market, and even if two slipped through, the
    interval is the cluster-robust one, never the binomial."""
    sql = " ".join(ms._SQL_EXIT_CENSUS.split())
    assert "DISTINCT ON (whale, condition_id)" in sql
    # a judged rest wins the market, then any rest, then the newest --
    # so a market whose newest tick says 'hold' does not erase the plan
    # it rested an hour ago
    assert ("ORDER BY whale, condition_id, (detail->>'exit_plan' = 'rest' AND "
            "would_fill IS NOT NULL) DESC, (detail->>'exit_plan' = 'rest') DESC, at DESC") in sql
    rows = []
    for i in range(6):
        rows.append({"whale": "rn1", "condition_id": f"0x{i % 2}", "exit_kind": "reduced",
                     "exit_plan": "rest", "would_fill": i % 2 == 0, "would_qty": 10,
                     "would_px": 0.3, "family": "moneyline", "touched_s": 12.0})
    out = ms.summarize_exit_rows(rows)["whales"]["rn1"]
    assert out["resolved"] == 6 and out["fills"] == 3
    assert out["clusters"] == 2, "six readings of two markets are two clusters"
    # and the gate's own n is that CLUSTER count, so re-reading a market
    # cannot buy a second market's worth of evidence: six resolved
    # readings of two markets are below the floor, and below the floor
    # there is no rate and no lower bound to quote at all
    assert out["ready"] is False and out["rate"] is None and out["lo"] is None
    # and the reading is per whale, not pooled
    rows2 = rows + [dict(rows[0], whale="homerunhazard", condition_id="0x9")]
    both = ms.summarize_exit_rows(rows2)["whales"]
    assert set(both) == {"rn1", "homerunhazard"} and both["homerunhazard"]["n"] == 1


def test_exit_unjudged_is_counted(monkeypatch):
    """A row it cannot judge is `unjudged`: never a fill, never a miss.
    Two shapes -- a rest nobody read back, and a reading whose rule
    could not decide at all (an unreadable venue, a frozen slug, no
    ratio, no mark). Every market lands in exactly one bucket."""
    _nosleep(monkeypatch)
    _at_now(monkeypatch)
    led = [{"sh": 100.0, "intent": "ORDER_INTENT_BUY_LONG"}]
    # the venue walk failed: the plan cannot be formed, the exit is named
    row = _run(ms.shadow_market(_Pool(fills=EXITFILLS, ledger_rows=led), _Pmus(bid=0.26, ask=0.29),
                                "rn1", CID, RATIO, {}, positions=None))
    assert row["detail"]["exit_plan"] == "none" and row["detail"]["exit_reason"] == "venue unreadable"
    assert row["detail"]["exit_kind"] == "reduced"          # the evidence is still recorded
    # and with nothing on our ledger -- the mirror's own state today --
    # it is still a refusal, not a decision not to reduce
    unread0 = _run(ms.shadow_market(_Pool(fills=EXITFILLS), _Pmus(bid=0.26, ask=0.29),
                                    "rn1", CID, RATIO, {}, positions=None))
    assert unread0["detail"]["exit_ledger"] == 0.0 and unread0["detail"]["exit_target"] == 34
    assert unread0["detail"]["exit_plan"] == "none"
    assert unread0["detail"]["exit_reason"] == "venue unreadable"
    # a frozen slug is the same class: a reading that could not decide.
    # BOTH SIGNS OF (target - ledger), because the bucket is decided by
    # the REASON and never by that comparison: with our ledger at 100
    # the target (34) sits below it, with our ledger at 0 -- the
    # mirror's actual state, holding nothing -- it sits above, and a
    # refusal is a refusal either way.
    frozen = _run(ms.shadow_market(_Pool(fills=EXITFILLS, ledger_rows=led), _Pmus(bid=0.26, ask=0.29),
                                   "rn1", CID, RATIO, {}, positions={EXIT_SLUG: 3.0}))
    assert frozen["detail"]["exit_ledger"] == 100.0 and frozen["detail"]["exit_target"] == 34
    assert frozen["detail"]["exit_plan"] == "none"
    assert frozen["detail"]["exit_reason"].startswith("frozen")
    froz0 = _run(ms.shadow_market(_Pool(fills=EXITFILLS), _Pmus(bid=0.26, ask=0.29),
                                  "rn1", CID, RATIO, {}, positions={EXIT_SLUG: 3.0}))
    assert froz0["detail"]["exit_ledger"] == 0.0 and froz0["detail"]["exit_target"] == 34
    assert froz0["detail"]["exit_plan"] == "none", "target >= ledger does not make a refusal a hold"
    assert froz0["detail"]["exit_reason"].startswith("frozen")
    # an unreadable book: no mark, so no plan -- and the exit still counted
    nomark = _run(ms.shadow_market(_Pool(fills=EXITFILLS, ledger_rows=led), _Pmus(raise_bbo=True),
                                   "rn1", CID, RATIO, {}, positions={EXIT_SLUG: 100.0}))
    assert nomark["detail"]["exit_plan"] == "none"
    assert nomark["detail"]["exit_reason"].startswith("no mark")
    assert nomark["detail"]["exit_complement_px"] == 0.68
    # no ratio: the same
    noratio = _run(ms.shadow_market(_Pool(fills=EXITFILLS, ledger_rows=led), _Pmus(bid=0.26, ask=0.29),
                                    "rn1", CID, None, {}, positions={EXIT_SLUG: 100.0}))
    assert noratio["detail"]["exit_plan"] == "none"
    assert noratio["detail"]["exit_reason"].startswith("no ratio")
    # in the census: one unresolved rest, one no-plan, one hold, one fill
    rows = [{"whale": "rn1", "condition_id": "a", "exit_kind": "reduced", "exit_plan": "rest",
             "would_fill": None, "would_qty": 10, "would_px": 0.3},
            {"whale": "rn1", "condition_id": "b", "exit_kind": "left", "exit_plan": "none",
             "exit_reason": "venue unreadable"},
            {"whale": "rn1", "condition_id": "c", "exit_kind": "reduced", "exit_plan": "hold",
             "exit_reason": "on target"},
            {"whale": "rn1", "condition_id": "d", "exit_kind": "reduced", "exit_plan": "rest",
             "would_fill": True, "would_qty": 10, "would_px": 0.3, "touched_s": 8.0}]
    b = ms.summarize_exit_rows(rows)["whales"]["rn1"]
    assert b["unjudged"] == 2 and b["unresolved"] == 1 and b["no_plan"] == 1
    assert b["resolved"] == 1 and b["fills"] == 1 and b["misses"] == 0
    assert b["n"] == b["resolved"] + b["unjudged"] + b["hold"] == 4
    assert b["reduced"] == 3 and b["left"] == 1
    # the COUNTS are facts and print at any n; the rate off one judged
    # market is not a reading and is not computed at all
    assert b["rate"] is None and b["lo"] is None and b["ready"] is False


def test_the_exit_leg_holds_when_there_is_nothing_of_ours_to_reduce(monkeypatch):
    """`hold` is a DECISION not to reduce and is not a miss: he trimmed,
    and either our target is still at or above what we hold, or the move
    is inside the bands the rules already refuse."""
    _nosleep(monkeypatch)
    _at_now(monkeypatch)
    # we hold nothing: his reduction leaves the entry leg the only rule
    row = _run(ms.shadow_market(_Pool(fills=EXITFILLS), _Pmus(bid=0.26, ask=0.29), "rn1", CID,
                                RATIO, {}, positions={}))
    assert row["would_side"] == "BUY_LONG"
    assert row["detail"]["exit_plan"] == "hold"
    assert "entry leg" in row["detail"]["exit_reason"]
    # on target: he trimmed and we are already where the ratio wants us
    led = [{"sh": 34.0, "intent": "ORDER_INTENT_BUY_LONG"}]
    on_t = _run(ms.shadow_market(_Pool(fills=EXITFILLS, ledger_rows=led), _Pmus(bid=0.26, ask=0.29),
                                 "rn1", CID, RATIO, {}, positions={EXIT_SLUG: 34.0}))
    assert on_t["detail"]["exit_plan"] == "hold" and on_t["detail"]["exit_reason"] == "on target"
    # the dollar dead band is a hold too, not an unjudged reading
    p = ms.mi.Plan(None, 0, None, "under the dollar dead band", None, {})
    ev = ms.reduction_event(EXITFILLS, M, N, now_ts=NOW)
    assert ms.exit_leg(ev, 40.0, 40.0, 34, p, 0.32)["exit_plan"] == "hold"
    # and no exit block at all on a market where he is not reducing
    assert ms.exit_leg(None, 0.0, 0.0) == {}


def test_the_exit_census_is_bounded_contained_and_never_costs_a_tick(monkeypatch):
    """A money-path rule the measurement side inherits: the refusal must
    be CONTAINED. This census is a read of our own rows -- bounded by a
    window, a market cap and a wall-clock timeout -- and a failure of it
    may not abandon a tick, spend venue budget or hammer the table."""
    _nosleep(monkeypatch)
    sql = " ".join(ms._SQL_EXIT_CENSUS.split())
    assert "at >= now() - ($1::float8 * interval '1 hour')" in sql and "LIMIT $2" in sql
    assert ms.EXIT_WINDOW_H == 24.0 and ms.EXIT_SUMMARY_MAX == 2000 and ms.EXIT_CENSUS_TIMEOUT_S == 15.0
    _reset_exit_cache()
    ms._backoff_until = 0.0
    p = _ExitPool(fills=EXITFILLS, whales_ratio_fills=_ratio_fills(),
                  census=[{"whale": "rn1", "condition_id": "a", "exit_kind": "left",
                           "exit_plan": "rest", "would_fill": True, "would_qty": 4,
                           "would_px": 0.5, "family": "moneyline", "touched_s": 30.0}])
    stats = _run(ms.tick_once(p, _Pmus(bid=0.26, ask=0.29, held={EXIT_SLUG: 0.0})))
    assert p.census_reads == 1
    census_q = [q for q in p.queries if "/* exit-leg-census */" in q[0]]
    # window and cap, both bound -- and the cap is asked for ONE ROW
    # MORE than it allows, which is how a census that hit its cap is
    # detectable at all
    assert len(census_q) == 1 and census_q[0][1] == (24.0, 2001)
    assert stats["exit_leg"]["rn1"]["fills"] == 1 and stats["exit_leg"]["rn1"]["n"] == 1
    # the split is published and carries the COUNTS at any n; its touch
    # times take its whale's floor, and one judged market is below it
    fb = stats["exit_family"]["rn1/moneyline"]
    assert fb["n"] == 1 and fb["fills"] == 1 and fb["touch_n"] == 1
    assert fb["ready"] is False and fb["touch_p50"] is None and fb["touch_p90"] is None
    assert stats["exit_census_age_s"] >= 0.0
    # the interval holds: the next tick reads the cache, not the table
    _run(ms.tick_once(p, _Pmus(bid=0.26, ask=0.29)))
    assert p.census_reads == 1, "the census is not a per-tick read"
    # A RAISING TABLE IS NAMED AND CONTAINED: no numbers, no abandon
    _reset_exit_cache()
    q = _ExitPool(fills=EXITFILLS, whales_ratio_fills=_ratio_fills(), census_raise=True)
    st = _run(ms.tick_once(q, _Pmus(bid=0.26, ask=0.29)))
    assert st["exit_leg"] == {"error": "RuntimeError"} and st["status"] == "ok"
    assert not st.get("abandoned") and st["rows"] >= 1
    # a slow one is the same, through the timeout, and the tick returns
    _reset_exit_cache()
    monkeypatch.setattr(ms, "EXIT_CENSUS_TIMEOUT_S", 0.01)
    h = _ExitPool(fills=EXITFILLS, whales_ratio_fills=_ratio_fills(), census_hangs=True)
    st2 = _run(ms.tick_once(h, _Pmus(bid=0.26, ask=0.29)))
    assert st2["exit_leg"] == {"error": "TimeoutError"} and not st2.get("abandoned")
    # and a failure takes the interval with it: no retry storm
    _run(ms.tick_once(h, _Pmus(bid=0.26, ask=0.29)))
    assert h.census_reads == 1


def test_a_truncated_census_is_not_a_reading_and_the_window_travels_with_it(monkeypatch):
    """Two bounds change what the number MEANS, so both travel with it.

    The cap is applied after `ORDER BY whale, condition_id`, so a
    census that hit it is not a sample: it is the alphabetically-first
    slice, and whichever whale sorts first takes every slot while the
    second reads n=0. This worker already holds the positions walk to
    that standard three functions up -- a walk that hit the page cap is
    not a reading of the account -- and a census is held to it here.

    The window is shell-settable down to an hour, so a line that does
    not name its window can serve a 1 h cohort in the shape of the 24 h
    reading §3b and §4-S4 are defined over."""
    _nosleep(monkeypatch)
    rows = [{"whale": "rn1", "condition_id": f"m{i}", "exit_kind": "reduced",
             "exit_plan": "rest", "would_fill": True, "would_qty": 10, "would_px": 0.4}
            for i in range(4)]
    p = _ExitPool(fills=EXITFILLS, census=rows)
    # the caller reads limit+1 and the summary refuses when that row exists
    val = _run(ms.exit_census(p, window_h=24.0, limit=3))
    assert p.queries[-1][1] == (24.0, 4), "one row past the cap, to see the cap was hit"
    assert val["truncated"] is True and val["whales"] == {} and val["families"] == {}
    ms._exit_cache.update(at=NOW, value=val)
    st: dict = {}
    ms.attach_exit_census(st, NOW)
    assert st["exit_leg"] == {"state": "truncated"}, "no numbers at all from a truncated census"
    assert st["exit_markets"] is None and st["exit_window_h"] == 24.0
    # exactly at the cap is a whole reading, not a truncated one
    val = _run(ms.exit_census(_ExitPool(fills=EXITFILLS, census=rows), window_h=24.0, limit=4))
    assert val["truncated"] is False and val["whales"]["rn1"]["n"] == 4
    # THE WINDOW IS PUBLISHED BESIDE THE NUMBERS: a 1 h reading and a
    # 24 h reading are not the same reading, and the line must say which
    short = _run(ms.exit_census(_ExitPool(fills=EXITFILLS, census=rows), window_h=1.0, limit=10))
    assert short["window_h"] == 1.0
    ms._exit_cache.update(at=NOW, value=short)
    st = {}
    ms.attach_exit_census(st, NOW)
    assert st["exit_window_h"] == 1.0
    # and the published block is a COPY: the cache outlives the tick, so
    # nothing downstream can edit the reading the next tick serves
    st["exit_leg"]["rn1"]["n"] = 99
    st["exit_family"].clear()
    assert ms._exit_cache["value"]["whales"]["rn1"]["n"] == 4
    assert ms._exit_cache["value"]["families"] != {}


def test_the_exit_reading_is_readable_per_whale_and_prints_its_own_minimum_n(monkeypatch):
    """The gate is that the line PRINTS at n >= 30 markets -- the VALUE
    is owner decision 18's input, not a threshold this unit sets. The
    block is scalars per whale so the public heartbeat serves it whole."""
    from sportsassets.api.app import _sanitize_detail as san

    def _rows(n):
        return [{"whale": "rn1", "condition_id": f"m{i}", "exit_kind": "reduced",
                 "exit_plan": "rest", "would_fill": i % 3 > 0, "would_qty": 20,
                 "would_px": 0.4, "family": "moneyline", "touched_s": float(i)}
                for i in range(n)]

    assert ms.EXIT_MIN_N == 30
    b29 = ms.summarize_exit_rows(_rows(29))["whales"]["rn1"]
    assert b29["n"] == 29 and b29["ready"] is False and b29["n_min"] == 30
    # below the floor the estimates are not computed at all -- only the
    # counts they would come from, which are facts
    assert b29["rate"] is None and b29["lo"] is None and b29["unfilled_usd_share"] is None
    assert b29["resolved"] == 29 and b29["fills"] == 19 and b29["clusters"] == 29
    # ...INCLUDING THE TOUCH-TIME PERCENTILES. §3b lists
    # `.time_to_touch_p50/p90` in the same gate row as `sell_fill_lo`, so
    # a line that is a gate obeys one floor in every field it serves: a
    # descriptive median off two markets printed beside `below_min_n`
    # siblings is still a number read off the gate row. The n behind
    # them is a count and stays.
    assert b29["touch_p50"] is None and b29["touch_p90"] is None
    assert b29["touch_n"] == 19, "the count behind them is a fact and prints"
    f29 = ms.summarize_exit_rows(_rows(29))["families"]["rn1/moneyline"]
    assert f29["ready"] is False and f29["touch_p50"] is None and f29["touch_n"] == 19, (
        "the split takes its whale's floor: otherwise it is the way to read "
        "the number the gate line refused")
    b30 = ms.summarize_exit_rows(_rows(30))["whales"]["rn1"]
    assert b30["ready"] is True and b30["clusters"] == 30
    # the clustered 95% LOWER bound, and it is below the point rate
    assert 0.0 <= b30["lo"] <= b30["rate"] <= 1.0
    # time to touch, and the dollars the exits would NOT have filled
    assert b30["touch_p50"] is not None and b30["touch_p90"] >= b30["touch_p50"]
    assert b30["touch_n"] == b30["fills"], "the percentiles print the n behind them"
    f30 = ms.summarize_exit_rows(_rows(30))["families"]["rn1/moneyline"]
    assert f30["ready"] is True and f30["touch_p50"] is not None
    assert b30["unfilled_usd"] == round(10 * 20 * 0.4, 2)
    assert b30["unfilled_usd_share"] == round(10 / 30, 4)
    # the public heartbeat keeps every one of them a number
    out = san({"exit_leg": {"rn1": b30}, "exit_family": {"rn1/moneyline": {"n": 30}}})
    assert out["exit_leg"]["rn1"]["lo"] == b30["lo"] and out["exit_leg"]["rn1"]["ready"] is True
    assert out["exit_family"]["rn1/moneyline"]["n"] == 30
    # and it fits that endpoint's own caps: a block over them is not an
    # error, it is a SILENTLY truncated one -- which is why both blocks
    # are flat scalars keyed by whale (and by whale/family)
    assert len(b30) < 40 and "_truncated_keys" not in out["exit_leg"]["rn1"]
    assert san({"exit_family": {"k": {"deep": {"x": 1}}}})["exit_family"]["k"]["deep"] == "<dict depth>"
    # a tick that has never read it says so rather than printing silence
    _reset_exit_cache()
    st = {}
    ms.attach_exit_census(st, NOW)
    assert st["exit_leg"] == {"state": "unread"}
    # and a tick that SKIPPED the refresh says that instead: "we never
    # read it" and "we did not read it on this tick" are different facts
    # about the instrument, and a venue outage produces the second
    for skipped in ("abandoned", "skipped_backoff", "switched_off"):
        st2: dict = {skipped: True}
        ms.attach_exit_census(st2, NOW)
        assert st2["exit_leg"] == {"state": "suppressed"}, skipped


def _exit_rows(hold=0, fills=0, misses=0, unjudged=0, whale="rn1"):
    """A census cohort with each bucket set independently: `n` counts
    all of them, the gate's own n counts only the judged ones."""
    rows = [{"whale": whale, "condition_id": f"h{i}", "exit_kind": "reduced",
             "exit_plan": "hold", "exit_reason": "on target"} for i in range(hold)]
    rows += [{"whale": whale, "condition_id": f"u{i}", "exit_kind": "reduced",
              "exit_plan": "none", "exit_reason": "frozen: venue and ledger disagree"}
             for i in range(unjudged)]
    rows += [{"whale": whale, "condition_id": f"f{i}", "exit_kind": "reduced",
              "exit_plan": "rest", "would_fill": True, "would_qty": 20, "would_px": 0.4,
              "family": "moneyline", "touched_s": 5.0} for i in range(fills)]
    rows += [{"whale": whale, "condition_id": f"m{i}", "exit_kind": "reduced",
              "exit_plan": "rest", "would_fill": False, "would_qty": 20, "would_px": 0.4,
              "family": "moneyline"} for i in range(misses)]
    return rows


def test_ready_keys_on_the_judged_markets_never_on_the_count_of_his_reductions():
    """§3b reads this gate as `proportion / market / >= 30`, so the 30 is
    the PROPORTION'S OWN DENOMINATOR -- markets we actually judged --
    not the number of markets he reduced in.

    Keyed on the reduction count, the flag whose stated job is that a
    reading below n=30 cannot be quoted as authorising anything went
    true with no reading at all behind it. It is the expected day-one
    shape, not a corner: the mirror is cancel-only and holds nothing, so
    most markets plan BUY_LONG and land in `hold`, and every refusal
    lands in `unjudged`."""
    b = ms.summarize_exit_rows(_exit_rows(hold=28, fills=2))["whales"]["rn1"]
    assert b["n"] == 30 and b["hold"] == 28 and b["resolved"] == 2 and b["clusters"] == 2
    assert b["ready"] is False, "28 holds and two fills are not thirty judged markets"
    assert b["rate"] is None and b["lo"] is None
    # THE NUMBER THAT WOULD HAVE BEEN SERVED. Two 0/1 observations that
    # both filled give a zero standard error, so the cluster-robust
    # interval collapses onto the point estimate and the line would have
    # asserted "at 95% confidence at least 100% of our exit rests fill"
    # in the field decision 18 reads. The estimator still does it; the
    # census no longer publishes it.
    ci = mr.rate_with_ci([{"would_fill": True, "condition_id": "f0"},
                          {"would_fill": True, "condition_id": "f1"}])
    assert ci["ci95"] == [1.0, 1.0] and ci["clusters"] == 2
    # thirty markets he reduced in, none of them judged
    b = ms.summarize_exit_rows(_exit_rows(hold=30))["whales"]["rn1"]
    assert b["n"] == 30 and b["ready"] is False and b["resolved"] == 0 and b["rate"] is None
    b = ms.summarize_exit_rows(_exit_rows(unjudged=30))["whales"]["rn1"]
    assert b["n"] == 30 and b["unjudged"] == 30 and b["ready"] is False
    # one judged market inside thirty
    b = ms.summarize_exit_rows(_exit_rows(hold=29, fills=1))["whales"]["rn1"]
    assert b["ready"] is False and b["rate"] is None
    # a busy day that is still one market short of the gate
    b = ms.summarize_exit_rows(_exit_rows(hold=371, fills=20, misses=9))["whales"]["rn1"]
    assert b["n"] == 400 and b["resolved"] == 29 and b["clusters"] == 29
    assert b["ready"] is False and b["rate"] is None and b["lo"] is None
    # and the gate funds on the thirtieth JUDGED market, whatever n is
    b = ms.summarize_exit_rows(_exit_rows(hold=370, fills=20, misses=10))["whales"]["rn1"]
    assert b["n"] == 400 and b["clusters"] == 30 and b["ready"] is True
    assert b["rate"] == round(20 / 30, 4) and 0.0 <= b["lo"] < b["rate"]
    assert b["unfilled_usd_share"] == round(10 / 30, 4)
    # per whale, not pooled: his thirty do not fund the other whale
    rows = _exit_rows(hold=370, fills=20, misses=10) + _exit_rows(fills=2, whale="hrh")
    both = ms.summarize_exit_rows(rows)["whales"]
    assert both["rn1"]["ready"] is True and both["hrh"]["ready"] is False
    assert both["hrh"]["rate"] is None and both["hrh"]["lo"] is None


def test_a_fail_closed_refusal_is_never_filed_as_a_decision_not_to_reduce():
    """A3's unreadable contract: a row it cannot judge is `unjudged`,
    never a fill and never a miss -- and never, either, a `hold`, which
    the line serves as "the rule chose not to reduce".

    `mi.plan` returns side=None for six reasons: four DECISIONS and two
    FAIL-CLOSED REFUSALS. The bucket is decided by which of those it is
    and by nothing else. An earlier version filed any side-None plan as
    `hold` when `target >= ledger` -- a comparison, not a decision --
    which swept `frozen` and `venue unreadable` into `hold` on exactly
    the state the mirror is in today (holding nothing, so the target is
    always at or above the ledger). Frozen is the most common reason
    class in the shadow window the mirror programme records."""
    import re
    reasons = set(re.findall(r'Plan\(None, 0, None, "([^"]+)"',
                             inspect.getsource(ms.mi.plan)))
    refusals = {"venue unreadable", "frozen: venue and ledger disagree"}
    assert reasons == set(ms._EXIT_HOLD_REASONS) | refusals, (
        "a seventh side-None reason appeared upstream: name it a decision or a refusal")
    ev = ms.reduction_event(EXITFILLS, M, N, now_ts=NOW)
    assert ev is not None
    for reason, venue in (("frozen: venue and ledger disagree", 3.0), ("venue unreadable", None)):
        p = ms.mi.Plan(None, 0, None, reason, None, {})
        # BOTH SIGNS of (target - ledger): a refusal is a refusal either way
        for ledger, target in ((0.0, 34), (100.0, 34), (34.0, 34)):
            out = ms.exit_leg(ev, ledger, venue, target, p, 0.32)
            assert out["exit_plan"] == "none", (reason, ledger, target)
            assert out["exit_reason"] == reason
            assert out["exit_kind"] == "reduced", "the evidence is still recorded"
    # the four DECISIONS are holds at both signs, unchanged
    for reason in ms._EXIT_HOLD_REASONS:
        p = ms.mi.Plan(None, 0, None, reason, None, {})
        for ledger, target in ((0.0, 34), (100.0, 34)):
            assert ms.exit_leg(ev, ledger, 100.0, target, p, 0.32)["exit_plan"] == "hold"
    # and a BUY_LONG is still a decision: the entry leg has the market
    buy = ms.mi.Plan("BUY_LONG", 34, 0.30, "increase toward target", None, {})
    assert ms.exit_leg(ev, 0.0, 0.0, 34, buy, 0.32)["exit_plan"] == "hold"
    # BUT THE PLAN'S OWN REASON IS NOT LOST. `mi.plan` returns a
    # BUY_LONG with `no price to rest at` when the bid is unreadable: the
    # BUCKET is right (the exit rule would not reduce here whatever the
    # book says) and this clause writes its own text over `p.reason`, so
    # without a second key the row would record only "the rule chose not
    # to reduce" and forget that there was no price at all.
    noprice = ms.mi.Plan("BUY_LONG", 34, None, "no price to rest at", None, {})
    out = ms.exit_leg(ev, 0.0, 0.0, 34, noprice, 0.32)
    assert out["exit_plan"] == "hold" and "entry leg" in out["exit_reason"]
    assert out["exit_plan_reason"] == "no price to rest at"
    # IT COMPOUNDS: a misfiled refusal lands in `hold`, `hold` lands in
    # `n`, and keying the gate on `n` let the rows carrying the least
    # information declare it funded. Both halves are closed, so 29
    # judged markets beside 300 frozen ones is not a reading.
    b = ms.summarize_exit_rows(_exit_rows(unjudged=300, fills=20, misses=9))["whales"]["rn1"]
    assert b["unjudged"] == 300 and b["hold"] == 0 and b["clusters"] == 29
    assert b["ready"] is False and b["rate"] is None


def test_the_exit_leg_sizes_from_the_mirrors_own_anchor_not_the_per_fill_clip(monkeypatch):
    """The mirror's sizing anchor is $50 and it is NOT the per-fill
    lane's clip (which is $250 for two whales today). The exit leg takes
    the ratio its caller was given and names no clip of its own, so a
    clip change cannot rescale an exit target."""
    from sportsassets.analytics import roster_rules

    assert roster_rules.MIRROR_ANCHOR_CLIP_USD == 50.0
    src = inspect.getsource(ms)
    for banned in ("per_fill_usd", "PER_FILL", "live_clip_overrides", "PENNY_TRIAL"):
        assert banned not in src, f"the mirror must not name the per-fill clip ({banned})"
    _nosleep(monkeypatch)
    _at_now(monkeypatch)
    # THE REAL CLIP, not an environment name nothing reads: the per-fill
    # lane's number is the hardcoded map plus the stored override the
    # rules worker writes (ingestion_state.live_clip_overrides), and
    # `per_fill_usd` reads the override ahead of the map. Sweeping both
    # is the only sweep that could fail.
    from sportsassets import live_executor as le

    assert le.PER_FILL_BY_WHALE.get("rn1") == 250.00, "the owner's clip today"
    assert le.per_fill_usd("rn1") == 250.00
    led = [{"sh": 100.0, "intent": "ORDER_INTENT_BUY_LONG"}]
    plans = []
    for clip in (50.0, 250.0, 1000.0):
        monkeypatch.setattr(le, "PER_FILL_BY_WHALE", dict(le.PER_FILL_BY_WHALE, rn1=clip))
        monkeypatch.setattr(le, "_clip_override", {"rn1": clip})
        # the executor's own ceiling still binds above $250; what
        # matters here is that the number the lane sizes from MOVED
        assert le.per_fill_usd("rn1") == min(clip, le.LIVE_MAX_CLIP_USD)
        row = _run(ms.shadow_market(_Pool(fills=EXITFILLS, ledger_rows=led),
                                    _Pmus(bid=0.26, ask=0.29), "rn1", CID, RATIO, {},
                                    positions={EXIT_SLUG: 100.0}))
        plans.append((row["detail"]["exit_qty"], row["detail"]["exit_px"], row["target"]))
    assert len(set(plans)) == 1 and plans[0] == (66, 0.32, 34)


def test_the_exit_leg_adds_no_venue_read_and_the_walk_stays_once_per_tick(monkeypatch):
    """Every venue read stays behind the pacer and the positions walk
    stays once per tick: the exit leg is read from fills we already
    hold, so it adds no read of any kind."""
    _nosleep(monkeypatch)
    _at_now(monkeypatch)
    _reset_exit_cache()
    ms._backoff_until = 0.0
    p = _ExitPool(fills=EXITFILLS, whales_ratio_fills=_ratio_fills(),
                  ledger_rows=[{"sh": 100.0, "intent": "ORDER_INTENT_BUY_LONG"}])
    pm = _Pmus(bid=0.26, ask=0.29, held={EXIT_SLUG: 100.0})
    stats = _run(ms.tick_once(p, pm))
    assert pm.portfolio.calls == 1, "one positions walk per tick"
    assert pm.calls == [("bbo", EXIT_SLUG)], "one BBO for the one market, nothing else"
    assert stats["exit_rows"] == 1 and stats["exit_rest"] == 1
    assert stats["exit_hold"] == 0 and stats["exit_unjudged"] == 0 and stats["exit_left"] == 0
    # and an unreadable walk still abandons the tick above every book read
    pm2 = _Pmus(raise_walk=True)
    ms._backoff_until = 0.0
    _reset_exit_cache()
    p2 = _ExitPool(fills=EXITFILLS)
    st2 = _run(ms.tick_once(p2, pm2, now_ts=NOW))
    assert st2["abandoned"] is True and st2["positions_unreadable"] is True and pm2.calls == []
    # AN ABANDONED TICK ADDS NO READ OF ANY KIND, the census included:
    # the tick has just told us the venue or the table is unwell and a
    # census read is not the answer to that
    assert p2.census_reads == 0
    # ...and the line says the reading was SUPPRESSED, not that the
    # instrument is silent
    assert st2["exit_leg"] == {"state": "suppressed"}
    # THE TWO ABANDONS THAT ACTUALLY REACH THE GUARD. `tick_once` has
    # three abandon paths and the walk-unreadable one above RETURNS
    # before `if not stats.get("abandoned"): await refresh_exit_census`
    # is ever evaluated -- so an assertion on that path alone passes
    # whether the guard exists or not, which is what the round-two
    # review measured (delete the guard: 58 passed). The BBO miss streak
    # and the write failure `break` out of the market loop and fall
    # THROUGH to that line, so they are the paths that pin it.
    for label, pool, pmus in (
            # three consecutive unreadable books: the venue is saying no
            ("miss_streak",
             _ExitPool(fills=EXITFILLS, whales_ratio_fills=_ratio_fills(),
                       conds=[f"c{i}" for i in range(5)]),
             _Pmus(raise_bbo=True, held={EXIT_SLUG: 0.0})),
            # the write raised: mirror_shadow itself is the suspect, and
            # the census reads that same table
            ("write_failed",
             _ExitPool(fills=EXITFILLS, whales_ratio_fills=_ratio_fills(),
                       conds=["c1", "c2", "c3"], write_raises=True),
             _Pmus(bid=0.26, ask=0.29, held={EXIT_SLUG: 0.0}))):
        ms._backoff_until = 0.0
        _reset_exit_cache()
        st = _run(ms.tick_once(pool, pmus, now_ts=NOW))
        assert st["abandoned"] is True, label
        assert pool.census_reads == 0, (
            f"{label}: this abandon falls THROUGH to the census guard, and the "
            "tick has just told us the venue or the table is unwell")
        assert st["exit_leg"] == {"state": "suppressed"}, label
    assert st["write_failed"] == "RuntimeError", "the write-failure path really ran"
    ms._backoff_until = 0.0
    _reset_exit_cache()
    # a cached reading is still served on an abandoned tick, with an age
    # that says how old it is
    ms._exit_cache.update(at=NOW - 100.0, value=ms.summarize_exit_rows(_exit_rows(fills=1)))
    ms._backoff_until = 0.0
    st3 = _run(ms.tick_once(_ExitPool(fills=EXITFILLS), _Pmus(raise_walk=True), now_ts=NOW))
    assert st3["abandoned"] is True and st3["exit_census_age_s"] == 100.0
    assert st3["exit_leg"]["rn1"]["n"] == 1
    ms._backoff_until = 0.0


# the two probe lines, byte-identical to mm/A3_yml.patch (the workflow
# is owned by nobody, so the patch is written beside this unit and the
# text is pinned here as well as there)
EXIT_PROBE = [
    '[ -s /tmp/hb_ms.json ] && jq -r \'def ts: if (.[0] | not) then "below_min_n" '
    'elif .[1] == null then "n/a" else "\\(.[1])s" end; '
    '[.[]? | select(.service == "mirror_shadow")][0].detail as $d '
    '| if $d == null then "  MIRROREXIT no heartbeat row — worker never completed a tick" '
    'elif ($d.exit_leg.error // $d.exit_leg.state) then "  MIRROREXIT census \\($d.exit_leg.error // $d.exit_leg.state) '
    '(age=\\([true, $d.exit_census_age_s] | ts))" '
    'elif (($d.exit_leg // {}) | length) == 0 then "  MIRROREXIT no market with a reduction in the window" '
    'else ($d.exit_leg | to_entries[] | "  MIRROREXIT \\(.key): mapped_markets_he_reduced=\\(.value.n // 0) '
    'window=\\($d.exit_window_h // "n/a")h gate_n=\\(.value.clusters // 0)/\\(.value.n_min // 30) '
    'ready=\\(.value.ready) reduced=\\(.value.reduced // 0) left=\\(.value.left // 0) rest=\\(.value.rest // 0) '
    'hold=\\(.value.hold // 0) unjudged=\\(.value.unjudged // 0) resolved=\\(.value.resolved // 0) '
    'fills=\\(.value.fills // 0) sell_fill=\\(if .value.ready then (.value.rate // "n/a") else "below_min_n" end) '
    'sell_fill_lo=\\(if .value.ready then (.value.lo // "n/a") else "below_min_n" end) '
    'time_to_touch_p50=\\([.value.ready, .value.touch_p50] | ts) '
    'p90=\\([.value.ready, .value.touch_p90] | ts) '
    'touch_n=\\(.value.touch_n // 0) '
    'unfilled_usd_share=\\(if .value.ready then (.value.unfilled_usd_share // "n/a") else "below_min_n" end) '
    'age=\\([true, $d.exit_census_age_s] | ts)") end\' '
    '/tmp/hb_ms.json 2>/dev/null || echo "  MIRROREXIT unavailable"',
    '[ -s /tmp/hb_ms.json ] && jq -r \'def ts: if (.[0] | not) then "below_min_n" '
    'elif .[1] == null then "n/a" else "\\(.[1])s" end; '
    '[.[]? | select(.service == "mirror_shadow")][0].detail as $d '
    '| if $d == null then "  MIRROREXITFAM no heartbeat row — worker never completed a tick" '
    'elif ($d.exit_leg.error // $d.exit_leg.state) then "  MIRROREXITFAM census \\($d.exit_leg.error // $d.exit_leg.state) '
    '(age=\\([true, $d.exit_census_age_s] | ts))" '
    'elif (($d.exit_family // {}) | length) == 0 then "  MIRROREXITFAM no judged family in the window" '
    'else ($d.exit_family | to_entries[] | "  MIRROREXITFAM \\(.key): n=\\(.value.n // 0) '
    'fills=\\(.value.fills // 0) '
    'time_to_touch_p50=\\([.value.ready, .value.touch_p50] | ts) '
    'p90=\\([.value.ready, .value.touch_p90] | ts) '
    'touch_n=\\(.value.touch_n // 0)") end\' '
    '/tmp/hb_ms.json 2>/dev/null || echo "  MIRROREXITFAM unavailable"',
]


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_the_exit_probe_lines_print_on_an_empty_endpoint_and_on_a_real_census(tmp_path):
    """Every new jq line parses on an EMPTY endpoint (A3) and none of
    them can print silence: an absent, empty or unreadable heartbeat
    prints its own tag, because a silent instrument is indistinguishable
    from a healthy quiet one."""
    def _run_lines(text: str) -> str:
        f = tmp_path / "hb.json"
        f.write_text(text)
        return "".join(
            subprocess.run(["bash", "-c", s.replace("/tmp/hb_ms.json", str(f))],
                           capture_output=True, text=True, timeout=30).stdout
            for s in EXIT_PROBE)

    assert "MIRROREXIT unavailable" in _run_lines("")
    assert "MIRROREXIT no heartbeat row" in _run_lines("[]")
    assert "MIRROREXIT census RuntimeError" in _run_lines(json.dumps(
        [{"service": "mirror_shadow", "detail": {"exit_leg": {"error": "RuntimeError"}}}]))
    assert "MIRROREXIT census unread" in _run_lines(json.dumps(
        [{"service": "mirror_shadow", "detail": {"exit_leg": {"state": "unread"}}}]))
    assert "no market with a reduction" in _run_lines(json.dumps(
        [{"service": "mirror_shadow", "detail": {"exit_leg": {}, "exit_census_age_s": 4}}]))
    assert "MIRROREXIT census truncated" in _run_lines(json.dumps(
        [{"service": "mirror_shadow", "detail": {"exit_leg": {"state": "truncated"}}}]))
    # NEITHER LINE MAY BE SILENT, and the FAM line used to be. It read
    # `.detail.exit_family // {} | to_entries[]`, which on an empty
    # object emits nothing AND EXITS 0, so the `|| echo ... unavailable`
    # fallback could not fire: on a truncated, errored, unread or
    # suppressed census the second line printed nothing at all while its
    # sibling named the state. It now names the state itself.
    for detail, want in (
            ({"exit_leg": {"state": "truncated"}}, "MIRROREXITFAM census truncated"),
            ({"exit_leg": {"error": "RuntimeError"}}, "MIRROREXITFAM census RuntimeError"),
            ({"exit_leg": {"state": "unread"}}, "MIRROREXITFAM census unread"),
            ({"exit_leg": {"state": "suppressed"}}, "MIRROREXITFAM census suppressed"),
            # a census with markets in it but no JUDGED family yet
            ({"exit_leg": {"rn1": {"n": 4}}, "exit_family": {}},
             "MIRROREXITFAM no judged family in the window"),
            ({"exit_leg": {}, "exit_census_age_s": 4}, "MIRROREXITFAM no judged family")):
        got = _run_lines(json.dumps([{"service": "mirror_shadow", "detail": detail}]))
        assert want in got, (detail, got)
    assert "MIRROREXITFAM no heartbeat row" in _run_lines("[]")
    assert "MIRROREXITFAM unavailable" in _run_lines("")
    rows = [{"whale": "rn1", "condition_id": f"m{i}", "exit_kind": "left" if i else "reduced",
             "exit_plan": "rest", "would_fill": i % 3 > 0, "would_qty": 20, "would_px": 0.4,
             "family": "moneyline", "touched_s": float(i)} for i in range(30)]
    census = ms.summarize_exit_rows(rows)

    def _render(c, window=24.0):
        return _run_lines(json.dumps([{"service": "mirror_shadow", "status": "ok",
                                       "detail": {"exit_leg": c["whales"],
                                                  "exit_family": c["families"],
                                                  "exit_window_h": window,
                                                  "exit_census_age_s": 12.0}}]))

    out = _render(census)
    # the field names are §3b's own (`sell_fill_lo`, `time_to_touch_*`),
    # the gate's n is the JUDGED-market count against its floor, and the
    # window the reading was taken over is on the line. The reduction
    # count names BOTH halves of what it is keyed on -- markets HE
    # reduced in, that we could MAP -- because M14's denominator is
    # "planned reductions" and this is a strict subset of it.
    assert ("MIRROREXIT rn1: mapped_markets_he_reduced=30 window=24.0h "
            "gate_n=30/30 ready=true") in out, out
    assert "resolved=30 fills=20 sell_fill=0.6667 sell_fill_lo=" in out
    assert "time_to_touch_p50=" in out and "touch_n=20" in out
    assert "MIRROREXITFAM rn1/moneyline: n=30 fills=20 time_to_touch_p50=" in out
    assert " rate=" not in out, "no per-family rate: §3b asks this split for touch times"
    # A SHORTER WINDOW IS A DIFFERENT READING and says so on the line
    assert "window=1.0h" in _render(ms.summarize_exit_rows(rows, window_h=1.0), window=1.0)
    # AND THE REVIEWER'S SCENARIO, RENDERED: 28 markets he reduced in
    # where the rule held, plus two filled exit rests. Thirty rows, no
    # reading -- and the line says so in every field that would have
    # carried one, instead of "ready=true ... sell_fill_lo=1.0".
    thin = _render(ms.summarize_exit_rows(_exit_rows(hold=28, fills=2)))
    assert ("MIRROREXIT rn1: mapped_markets_he_reduced=30 window=24.0h "
            "gate_n=2/30 ready=false") in thin, thin
    assert "sell_fill=below_min_n sell_fill_lo=below_min_n" in thin
    assert "unfilled_usd_share=below_min_n" in thin
    # EVERY ESTIMATE ON THE GATE LINE OBEYS ONE FLOOR. The touch-time
    # percentiles used to print at any n beside fields reading
    # `below_min_n` -- §3b lists them in the same gate row as
    # `sell_fill_lo` -- so a two-market median sat on the row decision 18
    # reads. The COUNT behind them still prints, because a count is a
    # fact; and the family split takes its whale's floor with it, or it
    # would have become the way to read the number the gate refused.
    assert "time_to_touch_p50=below_min_n p90=below_min_n touch_n=2" in thin, thin
    assert "MIRROREXITFAM rn1/moneyline: n=2 fills=2 time_to_touch_p50=below_min_n" in thin
    # once the patch lands, the workflow must carry the same text
    wf = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/engine-diagnostic.yml"
    body = wf.read_text() if wf.exists() else ""
    if "MIRROREXIT" in body:
        flat = " ".join(body.replace("\\\n", " ").split())
        for line in EXIT_PROBE:
            assert " ".join(line.split()) in flat, line
        # AND THE WORKFLOW'S OWN TEXT MUST RUN. Matching after flattening
        # is not enough to know the block works: a backslash continuation
        # placed INSIDE the single-quoted jq program reaches jq as a
        # literal backslash and is a syntax error, and the flattened
        # comparison cannot tell that apart from the working form (a raw
        # newline inside the quotes, which is what every other jq block
        # in that workflow uses). This runs the lines as written.
        lines = body.splitlines()
        i0 = next(i for i, s in enumerate(lines) if "MIRROREXIT-LINES-BEGIN" in s)
        i1 = next(i for i, s in enumerate(lines) if "MIRROREXIT-LINES-END" in s)
        blk = "\n".join(s for s in lines[i0 + 1:i1] if not s.strip().startswith("#"))
        f = tmp_path / "hb_wf.json"
        f.write_text(json.dumps([{"service": "mirror_shadow", "status": "ok",
                                  "detail": {"exit_leg": census["whales"],
                                             "exit_family": census["families"],
                                             "exit_window_h": 24.0,
                                             "exit_census_age_s": 12.0}}]))
        got = subprocess.run(["bash", "-c", blk.replace("/tmp/hb_ms.json", str(f))],
                             capture_output=True, text=True, timeout=30)
        assert ("MIRROREXIT rn1: mapped_markets_he_reduced=30 window=24.0h "
                "gate_n=30/30") in got.stdout, (got.stdout, got.stderr)
        assert "MIRROREXITFAM rn1/moneyline: n=30" in got.stdout
