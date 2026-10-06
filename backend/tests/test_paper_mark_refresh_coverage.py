"""CAPITAL-CRITICAL: THE HELD-MARK REFRESH COVERS EVERY HELD MARKET INSIDE THE
SLA, WITHIN AN EXPLICIT BUDGET, AND RECORDS EVERY ATTEMPT.

Over a seeded paper book (SYNTHETIC data in a scratch test database; the
venue is a fake transport -- no network, no order):

  * every due held market is read, stalest first, and the book goes from all
    STALE to >= 95% (here 100%) FRESH / QUIET_VALID;
  * the run is bounded (reads per run, the hourly budget, the time budget):
    what it could not reach is RECORDED as skipped with the bound, classified
    FEED_GAP with that reason -- never FRESH -- and the next run reaches it;
  * steady state, with a run every 60 s on a fake clock: the fresh rate over
    the markable book stays >= 95% at every instant after warm-up;
  * a failed read is recorded (FEED_GAP READ_FAILED), a TERMINAL market is not
    re-read and is EXTERNAL_UNAVAILABLE with its evidence, a book this process
    already read is harvested without a venue request;
  * the allocation rail: a strategy whose open positions cannot be freshly
    managed opens no new ENTRY (recorded refusal); management sales and other
    strategies are unaffected; a fresh book lets it grow again;
  * the read model (bettor_paper_freshness.read) and the equity wall's
    paper.freshness carry every count with its rule.
"""
from __future__ import annotations

import json

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import paper_mark_refresh as PMR

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
STRAT = "PINNACLE_COMPLETED_GAME_PAPER"
OTHER = "PINNACLE_EXPLORATION_PAPER"


class _Venue:
    """A fake book transport: every read answers a book with a bid; a slug
    in `fail` raises; the reads are counted in order."""

    def __init__(self, clock, *, fail=(), state=None):
        self.clock = clock
        self.fail = set(fail)
        self.calls = []
        self.state = state or {}
        self.n = 0

    def __call__(self, slug):
        self.calls.append(slug)
        self.n += 1
        if slug in self.fail:
            raise ConnectionError("synthetic venue failure")
        px = 0.40 + 0.01 * (self.n % 3)
        md = H.md(bids=[(px, 100)], offers=[(px + 0.02, 100)])
        if slug in self.state:
            md["state"] = self.state[slug]
        return {"marketData": md, "observed_at": self.clock()}


class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


async def _seed(conn, a, n, *, strategy=STRAT, at=H.T0, tag="m"):
    """n held positions, one market each, filled at `at` on a book observed
    at `at` + 3."""
    slugs = []
    for i in range(n):
        slug = "%s:%s%03d" % (a["account_id"], tag, i)
        o = H.order(a, key="%s%d" % (tag, i), qty=10, limit=0.50, slug=slug,
                    at=at, fixture="fx-%s-%d" % (tag, i),
                    group_id="paper_g_%s_%s%d" % (a["account_id"][-8:], tag,
                                                  i))
        o["strategy"] = strategy
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
        assert got["ok"], got
        await H.observe(conn, slug, at + 3, offers=[(0.50, 10)],
                        bids=[(0.48, 10)])
        f = await SIM.simulate_order(conn, got["order"]["order_id"],
                                     now=at + 4, fee_fn=H.zero_fee)
        assert f.get("filled_qty") or f.get("state") == "FILLED", f
        slugs.append(slug)
    return slugs


def _reset_hourly():
    PMR._HOURLY["reads"] = []


async def _classes(conn, a, now):
    rows = await PMF.classify_positions(conn, a["account_id"], now=now)
    return rows, PMF.summarize(rows)


@pg
async def test_one_run_refreshes_every_held_market_stalest_first():
    conn = await H.connect()
    try:
        _reset_hourly()
        a = await H.new_account(conn, "mrall")
        slugs = await _seed(conn, a, 30)
        # make five markets older still: they must be read first
        for s in slugs[25:]:
            await H.observe(conn, s, H.T0 - 500, bids=[(0.47, 10)])
        now = H.T0 + 1000
        rows, s0 = await _classes(conn, a, now)
        assert s0["counts"]["STALE"]["count"] == 30
        assert s0["fresh_rate"] == 0.0
        clk = _Clock(now)
        v = _Venue(clk)
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, recent=lambda *_, **k:
                                None)
        assert got.get("error") is None, got
        assert got["held_markets"] == 30 and got["due"] == 30
        assert got["read_attempted"] == 30 and got["read_ok"] == 30
        assert got["skipped_budget"] == 0
        assert sorted(v.calls) == sorted(slugs) and len(v.calls) == 30
        # no market starved: one read each, every one recorded
        run = await conn.fetchrow(
            "SELECT * FROM paper_mark_refresh_runs WHERE run_id = $1",
            got["run_id"])
        assert run["finished_at"] is not None and run["read_ok"] == 30
        oc = H.j(run["outcomes"])
        assert set(oc) == set(slugs)
        assert all(x["outcome"] == PMR.O_READ_OK for x in oc.values())
        rows, s1 = await _classes(conn, a, now + 1)
        assert s1["markable"] == 30
        assert s1["fresh_rate"] >= PMF.TARGET_FRESH_RATE
        assert s1["fresh_rate"] == 1.0
        assert {r["class"] for r in rows} <= set(PMF.FRESHLY_MANAGEABLE)
        # a second run right after reads nothing: nothing is due
        v2 = _Venue(clk)
        got2 = await PMR.refresh(conn, account_id=a["account_id"],
                                 market_data=G.PaperMarketDataClient(v2),
                                 now=now + 5, clock=clk,
                                 recent=lambda *_, **k: None)
        assert got2["due"] == 0 and got2["not_due"] == 30 and not v2.calls
    finally:
        await conn.close()


@pg
async def test_the_order_is_stalest_first_then_exposure():
    held = [{"us_market_slug": "a", "exposure_usd": 1.0},
            {"us_market_slug": "b", "exposure_usd": 50.0},
            {"us_market_slug": "c", "exposure_usd": 5.0},
            {"us_market_slug": "d", "exposure_usd": 5.0},
            {"us_market_slug": "e", "exposure_usd": 5.0}]
    now = H.T0
    ev = {"a": {"last_ok": {"obs_id": 1, "at": now - 900}},
          "b": {"last_ok": {"obs_id": 2, "at": now - 400}},
          "c": {"last_ok": None},
          "d": {"last_ok": {"obs_id": 3, "at": now - 100}},
          "e": {"last_ok": {"obs_id": 4, "at": now - 9000,
                            "market_state": "MARKET_STATE_EXPIRED"}}}
    pl = PMR.plan(held, ev, now=now)
    assert [s for s, _ in pl["due"]] == ["c", "a", "b"]
    assert pl["not_due"] == ["d"] and set(pl["terminal"]) == {"e"}


@pg
async def test_the_budget_bounds_a_run_and_what_it_skipped_is_recorded():
    conn = await H.connect()
    try:
        _reset_hourly()
        a = await H.new_account(conn, "mrcap")
        slugs = await _seed(conn, a, 20)
        now = H.T0 + 1000
        clk = _Clock(now)
        v = _Venue(clk)
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, max_reads=8,
                                recent=lambda *_, **k: None)
        assert got["read_attempted"] == 8 and len(v.calls) == 8
        assert got["skipped_budget"] == 12
        oc = got["outcomes"]
        skipped = [s for s, x in oc.items()
                   if x["outcome"] == PMR.O_SKIP_RUN_CAP]
        assert len(skipped) == 12
        rows, s = await _classes(conn, a, now + 1)
        by = {r["us_market_slug"]: r for r in rows}
        for sl in skipped:
            # a skipped market is FEED_GAP with the bound -- never fresh
            assert by[sl]["class"] == PMF.FEED_GAP
            assert by[sl]["reason"].startswith(PMR.O_SKIP_RUN_CAP)
        assert s["counts"]["FEED_GAP"]["count"] == 12
        assert s["freshly_manageable"] == 8
        # the hourly budget bounds too
        _reset_hourly()
        PMR._HOURLY["reads"] = [now] * 5
        v3 = _Venue(clk)
        got3 = await PMR.refresh(conn, account_id=a["account_id"],
                                 market_data=G.PaperMarketDataClient(v3),
                                 now=now + 2, clock=clk, hourly_budget=9,
                                 recent=lambda *_, **k: None)
        assert got3["read_attempted"] == 4 and len(v3.calls) == 4
        assert sum(1 for x in got3["outcomes"].values()
                   if x["outcome"] == PMR.O_SKIP_HOURLY) == 8
        # and the next run within budget reaches the rest
        _reset_hourly()
        v4 = _Venue(clk)
        got4 = await PMR.refresh(conn, account_id=a["account_id"],
                                 market_data=G.PaperMarketDataClient(v4),
                                 now=now + 3, clock=clk,
                                 recent=lambda *_, **k: None)
        assert got4["read_ok"] == 8 and got4["skipped_budget"] == 0
        rows, s = await _classes(conn, a, now + 4)
        assert s["fresh_rate"] == 1.0
    finally:
        _reset_hourly()
        await conn.close()


@pg
async def test_steady_state_keeps_the_book_fresh_inside_the_sla():
    """60 held markets, a refresh run every 60 s for 20 minutes on a fake
    clock: after the first run, the fresh rate over the markable book is
    >= 95% at every sampled instant (here: 100%)."""
    conn = await H.connect()
    try:
        _reset_hourly()
        a = await H.new_account(conn, "mrsteady")
        await _seed(conn, a, 60)
        t = H.T0 + 2000
        clk = _Clock(t)
        v = _Venue(clk)
        md = G.PaperMarketDataClient(v)
        rates = []
        for k in range(20):
            clk.t = t + 60.0 * k
            got = await PMR.refresh(conn, account_id=a["account_id"],
                                    market_data=md, now=clk.t, clock=clk,
                                    recent=lambda *_, **kw: None)
            assert got.get("error") is None, got
            for dt in (1.0, 30.0, 59.0):
                _, s = await _classes(conn, a, clk.t + dt)
                rates.append(s["fresh_rate"])
        assert min(rates) >= PMF.TARGET_FRESH_RATE, rates
        # and the reads are bounded: one per market per refresh interval,
        # well inside the explicit hourly budget
        assert len(v.calls) <= 60 * (20 * 60 / PMR.REFRESH_AFTER_S + 1)
        assert len(v.calls) < PMR.MAX_VENUE_READS_PER_HOUR
    finally:
        _reset_hourly()
        await conn.close()


@pg
async def test_failed_terminal_and_harvested_markets():
    conn = await H.connect()
    try:
        _reset_hourly()
        a = await H.new_account(conn, "mrkinds")
        slugs = await _seed(conn, a, 4)
        bad, term, shared, ok = slugs
        # the venue says this market EXPIRED (a successful read, no levels)
        await SIM.record_book(conn, slug=term, read={
            "marketData": {"bids": [], "offers": [],
                           "state": "MARKET_STATE_EXPIRED"},
            "observed_at": H.T0 + 500}, source="TEST", read_basis="TEST")
        now = H.T0 + 1000
        clk = _Clock(now)
        v = _Venue(clk, fail={bad})

        def recent(slug, *, max_age_s):
            if slug == shared:
                return {"marketData": H.md(bids=[(0.45, 10)]),
                        "observed_at": now - 20, "shared_read": True}
            return None
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, recent=recent)
        oc = got["outcomes"]
        assert oc[bad]["outcome"] == PMR.O_READ_FAILED
        assert oc[term]["outcome"] == PMR.O_SKIP_TERMINAL
        assert oc[shared]["outcome"] == PMR.O_HARVESTED
        assert oc[ok]["outcome"] == PMR.O_READ_OK
        # the terminal market was not re-read, the shared one not requested
        assert term not in v.calls and shared not in v.calls
        src = await conn.fetchval(
            "SELECT source FROM paper_book_observations WHERE obs_id=$1",
            oc[shared]["obs_id"])
        assert src.endswith(":SHARED_READ")
        rows, s = await _classes(conn, a, now + 1)
        by = {r["us_market_slug"]: r for r in rows}
        assert by[bad]["class"] == PMF.FEED_GAP
        assert by[bad]["reason"].startswith("READ_FAILED:")
        assert by[term]["class"] == PMF.EXTERNAL_UNAVAILABLE
        assert by[term]["evidence"]["market_state"] == "MARKET_STATE_EXPIRED"
        assert by[shared]["class"] in PMF.FRESHLY_MANAGEABLE
        assert by[shared]["mark"]["age_s"] == pytest.approx(21.0)
        assert by[ok]["class"] in PMF.FRESHLY_MANAGEABLE
        # the terminal market is excluded from markable
        assert s["markable"] == 3 and s["freshly_manageable"] == 2
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE ALLOCATION RAIL
# ═════════════════════════════════════════════════════════════════════

async def _refusals(conn, a, kind):
    return await conn.fetch(
        "SELECT * FROM paper_management_refusals WHERE account_id=$1 "
        "   AND kind=$2 ORDER BY refusal_id", a["account_id"], kind)


@pytest.fixture(autouse=True)
def _integrity_rail_isolated(monkeypatch):
    """The refresh and the stale-MARK rail are under test in this module; the
    management-integrity rail (packets / protection, strict) has its own
    tests in test_p0_xavier_packet_and_reconciliation."""
    _isolate_the_mark_rail(monkeypatch)


def _isolate_the_mark_rail(monkeypatch):
    """These tests exercise the STALE-MARK rate rail alone. The management-
    integrity rail beside it (packets / protection continuity, P0 closeout)
    has its own tests (test_p0_xavier_packet_and_reconciliation); here its
    verdict is held at "no refusal" so only the mark rate decides."""
    async def ok(conn, account_id, strategy, *, now):
        return {"refusal": None, "strategy": strategy}
    monkeypatch.setattr(PMF, "strategy_management_integrity", ok)


@pg
async def test_no_allocation_growth_where_management_is_stale(monkeypatch):
    _isolate_the_mark_rail(monkeypatch)
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "mrrail")
        slugs = await _seed(conn, a, 3)
        now = H.T0 + 1000                         # every mark 997 s old
        o = H.order(a, key="grow", qty=10, limit=0.50, at=now,
                    slug="%s:new" % a["account_id"], fixture="fx-new")
        o["strategy"] = STRAT
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=now)
        assert got["ok"] is False
        assert got["refusal"] == PMF.R_STALE_MANAGEMENT_BLOCKS_ALLOCATION
        assert got["stale_management_rate"] == 1.0
        assert got["max_stale_management_rate"] == \
            PMF.MAX_STALE_MANAGEMENT_RATE
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE idempotency_key=$1",
            o["idempotency_key"]) == 0
        rec = await _refusals(conn, a, PMF.K_ENTRY)
        assert len(rec) == 1 and rec[0]["refusal"] == got["refusal"]
        assert rec[0]["strategy"] == STRAT
        assert rec[0]["order_key"] == o["idempotency_key"]
        assert H.j(rec[0]["detail"])["counts"]["STALE"] == 3
        # ANOTHER strategy with no stale book is unaffected
        o2 = H.order(a, key="other", qty=10, limit=0.50, at=now,
                     slug="%s:oth" % a["account_id"], fixture="fx-oth")
        o2["strategy"] = OTHER
        assert (await L.submit_order(conn, o2, fee_fn=H.zero_fee,
                                     now=now))["ok"]
        # a management SALE of the stale book is never blocked by the rail
        sale = H.order(a, key="sell", direction="SELL", role="EXIT", qty=5,
                       limit=0.40, slug=slugs[0], at=now,
                       group_id="paper_g_%s_m0" % a["account_id"][-8:])
        sale["strategy"] = STRAT
        assert (await L.submit_order(conn, sale, now=now))["ok"]
        # fresh books for the strategy -> it may grow again
        for s in slugs:
            await H.observe(conn, s, now - 10, bids=[(0.47, 10)])
        o3 = dict(o, idempotency_key=o["idempotency_key"] + ":2",
                  order_id=None)
        o3.pop("order_id")
        got3 = await L.submit_order(conn, o3, fee_fn=H.zero_fee, now=now)
        assert got3["ok"], got3
    finally:
        await conn.close()


@pg
async def test_the_rail_counts_only_markable_positions_at_its_threshold(
        monkeypatch):
    _isolate_the_mark_rail(monkeypatch)
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "mrthr")
        slugs = await _seed(conn, a, 5)
        now = H.T0 + 1000
        # 4 fresh, 1 stale -> STRICT: refused until the stale one is fresh
        # or excluded (here: its market expires at the venue)
        for s in slugs[:4]:
            await H.observe(conn, s, now - 10, bids=[(0.47, 10)])
        o = H.order(a, key="g1", qty=10, limit=0.50, at=now,
                    slug="%s:n1" % a["account_id"], fixture="fx-n1")
        o["strategy"] = STRAT
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=now)
        assert got["ok"] is False
        assert got["refusal"] == PMF.R_STALE_MANAGEMENT_BLOCKS_ALLOCATION
        # the one stale market EXPIRED at the venue: excluded (with
        # evidence), never counted against the strategy
        await SIM.record_book(conn, slug=slugs[4], read={
            "marketData": {"bids": [], "offers": [],
                           "state": "MARKET_STATE_EXPIRED"},
            "observed_at": now - 600}, source="TEST", read_basis="TEST")
        m = await PMF.strategy_stale_management(conn, a["account_id"], STRAT,
                                                now=now)
        assert m["counts"]["EXTERNAL_UNAVAILABLE"] == 1
        # the new entry has no fill yet: it is not an open position
        assert m["markable"] == 4 and m["stale_management_rate"] == 0.0
        assert await PMF.allocation_refusal(
            conn, account_id=a["account_id"], strategy=STRAT,
            now=now) is None
        # two of the four go stale (> 300 s later): 50% > 20% -> refused
        later = now + 400
        for s in slugs[:2]:
            await H.observe(conn, s, later - 5, bids=[(0.46, 10)])
        ref = await PMF.allocation_refusal(
            conn, account_id=a["account_id"], strategy=STRAT, now=later)
        assert ref["refusal"] == PMF.R_STALE_MANAGEMENT_BLOCKS_ALLOCATION
        assert ref["stale_management_rate"] == pytest.approx(0.5)
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE READ MODEL AND THE EQUITY WALL
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_read_model_carries_every_position_and_every_rule():
    from sportsassets.api import command_equity as CE
    conn = await H.connect()
    try:
        _reset_hourly()
        a = await H.new_account(conn, "mrread")
        slugs = await _seed(conn, a, 3)
        now = H.T0 + 1000
        got = await PMF.read(conn, a["account_id"], now=now)
        assert got["status"] == "OK"
        assert got["open_positions"] == got["classified"] == 3
        assert got["counts"]["STALE"]["count"] == 3
        for c in PMF.CLASSES:
            assert got["counts"][c]["rule"] == PMF.RULES[c]
        r = got["positions"][0]
        for k in ("class", "reason", "rule", "mark", "valuation", "residual",
                  "settlement", "protection", "exposure_usd"):
            assert k in r, k
        assert r["mark"]["source"].startswith("paper_book_observations:")
        assert r["mark"]["age_s"] == pytest.approx(997.0)
        assert r["residual"]["reconciled"] is True
        assert r["residual"]["ledger_open_qty"] == pytest.approx(10.0)
        assert r["protection"]["state"] == "UNPROTECTED_NO_STANDING_ORDER"
        # harness orders carry no entry decision: no settlement identity,
        # stated, never invented
        assert r["settlement"]["fingerprint"] is None
        assert r["valuation"]["valuation_id"] is None
        assert got["refresh"]["latest_run"] is None
        assert got["refresh"]["why"] == "NO_HELD_MARK_REFRESH_RUN_RECORDED"
        clk = _Clock(now)
        await PMR.refresh(conn, account_id=a["account_id"],
                          market_data=G.PaperMarketDataClient(_Venue(clk)),
                          now=now, clock=clk, recent=lambda *_, **k: None)
        got = await PMF.read(conn, a["account_id"], now=now + 1)
        assert got["fresh_rate"] == 1.0 and got["meets_target"] is True
        assert got["refresh"]["latest_run"]["read_ok"] == 3
        # the equity wall's paper section (read inside READ ONLY)
        tr = await CE._readonly(conn)
        try:
            paper = await CE.read_paper(conn, now=now + 1,
                                        account_id=a["account_id"])
        finally:
            await tr.rollback()
        f = paper["freshness"]
        assert f["status"] == "OK" and f["open_positions"] == 3
        assert f["counts"]["FRESH"]["rule"] == PMF.RULES["FRESH"]
        assert f["fresh_rate"] == 1.0
        assert f["positions_route"] == "/api/command/paper/freshness"
        assert "positions" not in f
        json.dumps(f, default=str)
    finally:
        _reset_hourly()
        await conn.close()


class _Broken:
    def transaction(self, *a, **k):
        raise RuntimeError("no database")


async def test_an_unread_book_is_unavailable_never_zero():
    got = await PMF.read(_Broken(), "paper_x", now=H.T0)
    assert got["status"] == "UNAVAILABLE"
    assert got["counts"] is None and got["fresh_rate"] is None
    assert got["why"].startswith("FRESHNESS_READ_FAILED")


def test_the_freshness_route_is_get_only_and_requires_a_session():
    from fastapi.testclient import TestClient
    from sportsassets.api import app as APP
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get("/api/command/paper/freshness").status_code == 401
    assert client.post("/api/command/paper/freshness").status_code in (
        401, 405)


def test_the_budget_is_explicit_and_bounded():
    b = PMR.budget()
    assert b["refresh_after_s"] < b["sla_s"] == L.MARK_STALE_AFTER_S
    assert b["max_reads_per_run"] > 0 and b["run_budget_s"] > 0
    assert b["max_venue_reads_per_hour"] == PMR.MAX_VENUE_READS_PER_HOUR
    assert PMR.MAX_VENUE_READS_PER_HOUR <= 3600
    assert PMR.MIN_RUN_INTERVAL_S >= 30.0
