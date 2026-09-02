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
                      {"C": {"netPosition": "bad"}, "D": {"netPosition": 2.5}}])
    out = _run(ms.account_positions(pm))
    assert out == {"a": 10.0, "b": -3.0, "d": 2.5}
    # two pages, two paced reads: every venue call goes through the gate
    assert pm.portfolio.calls == 2 and slept.count(ms.READ_PACING_S) == 2
    assert _run(ms.account_positions(_Pmus(raise_walk=True))) is None


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


def test_a_plan_is_judged_against_the_next_reading_of_its_market(monkeypatch):
    _nosleep(monkeypatch)

    class _P(_Pool):
        def __init__(self, prev):
            super().__init__(fills=HIS)
            self.prev = prev
            self.updates = []

        async def fetchrow(self, sql, *a):
            if "/* prev-plan */" in sql:
                return self.prev
            return None

        async def execute(self, sql, *a):
            if "UPDATE mirror_shadow SET would_fill" in sql:
                self.updates.append(a)
            await super().execute(sql, *a)

    # previous tick would have BOUGHT at 0.30; this tick the ask came down to 0.30
    p = _P({"id": 9, "would_side": "BUY_LONG", "would_px": 0.30})
    _run(ms._write(p, {"whale": "rn1", "condition_id": CID, "bid": 0.29, "ask": 0.30,
                       "detail": {}}))
    assert p.updates == [(9, True)]
    # a sell at 0.55: the bid only reached 0.54 -> not filled
    p2 = _P({"id": 10, "would_side": "SELL_LONG", "would_px": 0.55})
    _run(ms._write(p2, {"whale": "rn1", "condition_id": CID, "bid": 0.54, "ask": 0.56,
                        "detail": {}}))
    assert p2.updates == [(10, False)]
    # an unreadable book judges nothing
    p3 = _P({"id": 11, "would_side": "BUY_LONG", "would_px": 0.30})
    _run(ms._write(p3, {"whale": "rn1", "condition_id": CID, "bid": None, "ask": None,
                        "detail": {}}))
    assert p3.updates == []
    # and the report's fill rate is over RESOLVED plans only
    rows = [{"us_market_slug": "s", "would_side": "BUY_LONG", "would_fill": True},
            {"us_market_slug": "s", "would_side": "BUY_LONG", "would_fill": None},
            {"us_market_slug": "s", "would_side": "SELL_LONG", "would_fill": False}]
    out = mr.summarize(rows, rows, {})
    assert out["would_orders"] == 3 and out["would_resolved"] == 2 and out["would_fill_rate"] == 0.5


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

def test_a_stale_previous_plan_is_not_judged_and_a_pulled_quote_is_not_a_fill(monkeypatch):
    _nosleep(monkeypatch)
    seen = []

    class _P(_Pool):
        async def fetchrow(self, sql, *a):
            if "/* prev-plan */" in sql:
                seen.append((" ".join(sql.split()), a))
            return None

    p = _P(fills=HIS)
    assert _run(ms._write(p, {"whale": "rn1", "condition_id": CID, "bid": 0.29, "ask": 0.30,
                              "detail": {}})) is None
    sql, args = seen[0]
    # only a plan written inside three ticks is judged against this book
    assert "AND at >= now() - ($3::float8 * interval '1 second')" in sql
    assert args[2] == ms.JUDGE_MAX_AGE_S == 3.0 * ms.POLL_S

    class _P2(_Pool):
        def __init__(self):
            super().__init__(fills=HIS)
            self.updates = []

        async def fetchrow(self, sql, *a):
            if "/* prev-plan */" in sql:
                return {"id": 5, "would_side": "BUY_LONG", "would_px": 0.30}
            return None

        async def execute(self, sql, *a):
            if "UPDATE mirror_shadow SET would_fill" in sql:
                self.updates.append(a)
            await super().execute(sql, *a)

    # BUY resting at 0.30; the bid was pulled to 0.29 and the ask stayed
    # 0.32: the level moved away, nobody came to our price -> NOT a fill
    p2 = _P2()
    assert _run(ms._write(p2, {"whale": "rn1", "condition_id": CID, "bid": 0.29, "ask": 0.32,
                               "detail": {}})) is False
    assert p2.updates == [(5, False)]
    # the side that has to reach us is unread -> nothing judged
    p3 = _P2()
    assert _run(ms._write(p3, {"whale": "rn1", "condition_id": CID, "bid": 0.29, "ask": None,
                               "detail": {}})) is None
    assert p3.updates == []


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
