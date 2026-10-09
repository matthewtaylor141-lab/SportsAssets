"""THE RESEARCH COLLECTOR'S ROTATION DRAWS THE WHOLE SLICE (V3, 2026-10-09).

THIS IS THE RESEARCH COLLECTOR, NOT TRADING COVERAGE: which markets the
read-only unselected state capture samples decides what the pre-registered
analysis observes, never what BETTOR can trade.

PRODUCTION (sportsassets-workers, 2026-10-09 04:25-05:19Z): every ~2.5 min

    ERROR bettor_state: slice 764 holds 49 markets against a cap of 40 --
          the cap is binding, so the rotation is drawing a fixed panel and
          9 markets are never sampled. The rule needs a version bump.

slices 764-774 held 46-56 markets. V2 drew in_slice[rot:rot+40] with `rot`
advancing ONE place per 65.58 h rotation, so the markets past the cap were
the same for weeks. V3 draws the next cap-sized window each visit -- every
market of the slice within ceil(n / cap) visits -- at the same read rate,
records its version on every row, and logs the binding cap once per change.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sportsassets import bettor_state_capture as sc
from sportsassets.workers import bettor_state as w

T0 = datetime(2026, 10, 9, 4, 25, 0, tzinfo=timezone.utc)
BOOK = {"bids": [{"px": {"value": "0.52"}, "qty": "120"}],
        "offers": [{"px": {"value": "0.55"}, "qty": "60"}],
        "stats": {"sharesTraded": "900"},
        "state": "MARKET_STATE_OPEN"}


def _cands(n):
    return [{"identifier": "mkt-%06d" % i, "symbol": "mkt-%06d" % i,
             "marketId": "mkt-%06d" % i, "eventId": "ev-%d" % (i // 7),
             "outcomeLeg": "yes", "kind": "aec", "sport": "football",
             "league": "nfl", "gameStart": None, "legIdentifier": None}
            for i in range(n)]


#: ~41,000 eligible markets, the size the 2026-10-09 slices imply (~52 a
#: slice over 787 slices)
UNIVERSE = _cands(41_000)


def _slice_members(at):
    cyc = sc.cycle_of(at)
    return {c["identifier"] for c in UNIVERSE
            if sc.slice_of(c["identifier"]) == cyc}


def test_a_slice_past_the_cap_is_drawn_whole_within_its_visits():
    """THE DEFECT. A 50-odd-market slice against a cap of 40: under V2 the
    markets past the cap were the same on every visit for weeks. Under V3
    every one is drawn within ceil(n / cap) visits."""
    checked = 0
    for k in range(40):
        at = T0 + timedelta(seconds=k * sc.SAMPLING_CADENCE_S)
        members = _slice_members(at)
        n = len(members)
        if n <= sc.MAX_MARKETS_PER_CYCLE:
            continue
        visits = -(-n // sc.MAX_MARKETS_PER_CYCLE)
        drawn = set()
        for v in range(visits):
            # the same slice, v full rotations later
            at_v = at + timedelta(seconds=v * sc.FULL_ROTATION_S)
            assert sc.cycle_of(at_v) == sc.cycle_of(at)
            sel = sc.select(UNIVERSE, at=at_v)
            assert sel["CAP_BINDING"] is True
            assert sel["VISITS_TO_COVER_SLICE"] == visits
            got = [c["identifier"] for c in sel["SELECTED"]]
            assert len(got) == sc.MAX_MARKETS_PER_CYCLE == len(set(got))
            drawn |= set(got)
        assert drawn == members, (n, len(members - drawn))
        checked += 1
    assert checked >= 10, "the fixture never made the cap bind"


def test_consecutive_visits_draw_consecutive_windows():
    at = T0
    while len(_slice_members(at)) <= sc.MAX_MARKETS_PER_CYCLE:
        at += timedelta(seconds=sc.SAMPLING_CADENCE_S)
    order = sorted(_slice_members(at), key=sc._order_key)
    n, cap = len(order), sc.MAX_MARKETS_PER_CYCLE
    a = sc.select(UNIVERSE, at=at)
    b = sc.select(UNIVERSE, at=at + timedelta(seconds=sc.FULL_ROTATION_S))
    assert b["SLICE_VISIT"] == a["SLICE_VISIT"] + 1
    assert b["WINDOW_START"] == (a["WINDOW_START"] + cap) % n
    for sel in (a, b):
        s0 = sel["WINDOW_START"]
        want = [order[(s0 + i) % n] for i in range(cap)]
        assert [c["identifier"] for c in sel["SELECTED"]] == want


def test_the_read_rate_and_the_cap_are_unchanged():
    """Same request rate: the cap, the per-tick share, the per-tick read
    ceiling are what they were; a bucket never reads more than the cap."""
    assert sc.MAX_MARKETS_PER_CYCLE == 40
    assert sc.TICKS_PER_BUCKET == 5 and sc.MAX_MARKETS_PER_TICK == 8
    assert w.MAX_READS_PER_TICK == sc.MAX_MARKETS_PER_TICK + 2
    assert sc.SAMPLING_CADENCE_S == 300 and sc.ROTATION_SLICES == 787
    for k in range(10):
        at = T0 + timedelta(seconds=k * sc.SAMPLING_CADENCE_S)
        whole = sc.select(UNIVERSE, at=at)["SELECTED"]
        assert len(whole) <= sc.MAX_MARKETS_PER_CYCLE
        ticks = []
        for t in range(sc.TICKS_PER_BUCKET):
            share = sc.select(UNIVERSE, at=at, tick=t)["SELECTED"]
            assert len(share) <= sc.MAX_MARKETS_PER_TICK
            ticks += share
        assert [c["identifier"] for c in ticks] == \
            [c["identifier"] for c in whole]


def test_a_slice_within_the_cap_is_read_whole_and_its_start_still_moves():
    small = _cands(9_700)
    a = sc.select(small, at=T0)
    b = sc.select(small, at=T0 + timedelta(seconds=sc.FULL_ROTATION_S))
    assert a["CAP_BINDING"] is False and a["SLICE_TRUNCATED_BY"] == 0
    assert {c["identifier"] for c in a["SELECTED"]} == \
        {c["identifier"] for c in b["SELECTED"]}
    if a["CANDIDATES_IN_SLICE"] > 1:
        assert [c["identifier"] for c in a["SELECTED"]] != \
            [c["identifier"] for c in b["SELECTED"]]


def test_the_partition_is_v2s_so_no_market_moves_slice():
    for ident in ("aec-nfl-buf-lar-2026-10-12", "mkt-000001", "x"):
        raw = "BETTOR_UNSELECTED_STATE_V2|%s" % ident
        want = int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16) \
            % sc.ROTATION_SLICES
        assert sc.slice_of(ident) == want


def test_the_rule_version_moved_and_is_on_every_row():
    assert sc.UNIVERSE_VERSION == "BETTOR_UNSELECTED_STATE_V3"
    v2 = sc.SUPERSEDED["BETTOR_UNSELECTED_STATE_V2"]
    assert v2["rowsRetained"] is True
    assert v2["rowsAreASampleOfTheUniverse"] is False
    assert v2["outcomesConsultedForTheChange"] is False
    assert "THE_CAP_BOUND" in v2["why"]
    assert "(visit x cap) mod n" in sc.FROZEN_RULE["ROTATION_METHOD"]
    sel = sc.select(UNIVERSE, at=T0)
    row = sc.state_record(sel["SELECTED"][0], observed_at=T0, book=BOOK,
                          selection=sel)
    assert row["UNIVERSE_VERSION"] == sc.UNIVERSE_VERSION
    assert row["RULE_SHA"] == sc.RULE_SHA


def test_it_says_it_is_the_research_collector_not_trading_coverage():
    d = sc.describe()
    assert "never what is tradable" in d["isResearchCollectorNotTradingCoverage"]
    assert "RESEARCH COLLECTOR, NOT TRADING COVERAGE" in w.__doc__


# ── the worker: logged once per change, the tick is not a failure ────────

class _Pool:
    async def fetch(self, *a, **k):
        return []

    async def fetchrow(self, *a, **k):
        return None

    async def fetchval(self, *a, **k):
        return 0

    async def execute(self, *a, **k):
        return None


def test_the_binding_cap_is_logged_once_per_change_not_every_tick(
        monkeypatch, caplog):
    """V2 wrote an ERROR "the rule needs a version bump" every ~2.5 min.
    V3 writes one line when the binding state changes; every tick still
    carries the state on its heartbeat."""
    async def cands(pool):
        return list(UNIVERSE)

    monkeypatch.setattr(w, "_candidates", cands)
    monkeypatch.setattr(w, "_read_book", lambda slug, pacing=1.0: {
        "marketData": BOOK, "feed": "book", "error": None})
    if hasattr(w, "_CAP_BINDING_LOGGED"):
        monkeypatch.setattr(w, "_CAP_BINDING_LOGGED", {"binding": None})
    caplog.set_level(logging.INFO, logger="sportsassets.workers.bettor_state")
    stats = [asyncio.run(w.tick(_Pool())) for _ in range(3)]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, [r.getMessage() for r in errors]
    binds = [r for r in caplog.records if "binds" in r.getMessage()]
    assert len(binds) == 1
    assert "research collector, not trading coverage" in binds[0].getMessage()
    for s in stats:
        assert s["status"] != "slice_truncated"
        assert s["universe"] == sc.UNIVERSE_VERSION
        assert s["capBinding"] is True and s["visitsToCoverSlice"] >= 2
        assert s["capPerCycle"] == sc.MAX_MARKETS_PER_CYCLE
