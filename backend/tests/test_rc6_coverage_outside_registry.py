"""RC6 LANE D2 (review finding 2): THE MARKETS KEPT OUT OF THE REGISTRY BY
NAME STAY COUNTED, BY CODE, WITH THE FULL PASS'S TIME.

The slug grammar (tests/test_rc6_coverage_slug_grammar.py) reads the venue
league code off the event slug's first segment, so the venue's BTC price
markets and its macro-economic / central-bank markets (`uscpi`, `usfed`,
`cut`, `hike`, `ecb`, `boj`, ... -- about 1,899 active rows in
research-sql 37871119335 / 37871400151) are NON_SPORTS by name and leave the
registry, and with it the scored denominator snapshot.coverage.active.
populate counted them only in the pass that read them: state["populate"]
is replaced by the incremental pass every POPULATE_EVERY_S (10 s, rows
changed since the watermark), while only the full pass (every 1,800 s)
reads every non-sports market, so a 60 s snapshot carried the full count
only by chance; and the waterfall never sees a retired row.

Now:
  * populate(excluded_detail=True) also counts, by code, the excluded
    markets the venue LISTS as active (`excluded_listed_active`: what the
    registry's active count would hold were the code a sports league);
  * the plane keeps the last FULL pass's record (populate.full_pass_record:
    by code, totals, the pass time, the rule) under its own state key,
    `populate_full`, which no incremental pass overwrites
    (universal_market_plane.record_populate), and publishes it in the
    snapshot (`populate_full`);
  * the coverage waterfall carries it as `outside_registry`
    (NON_SPORTS_EXCLUDED_FROM_REGISTRY), beside the sums and never in them.

Off (excluded_detail False, no outside_registry), populate's and the
pass's output are the RC5 output exactly.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import asyncpg
import pytest

from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import waterfall as WF
from sportsassets.workers import universal_market_plane as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def run(coro):
    return asyncio.run(coro)


FULL = {"read": 74_288, "upserted": 72_389, "changed": 12, "retired": 0,
        "full": True, "required_added": 0, "watermark": 1.0,
        "excluded": {"btc": 1_350, "uscpi": 40, "cut": 9},
        "excluded_listed_active": {"btc": 1_343, "uscpi": 38, "cut": 9}}
INCREMENTAL = {"read": 6, "upserted": 6, "changed": 2, "retired": 0,
               "full": False, "required_added": 0, "watermark": 2.0,
               "excluded": {"btc": 1}}


# ── 1. the record survives the incremental passes ────────────────────────

def test_the_full_pass_record_survives_an_incremental_pass():
    state: dict = {}
    W.record_populate(state, dict(FULL), full=True, now=1_000.0)
    rec = dict(state["populate_full"])
    assert rec["at"] == 1_000.0 and rec["full"] is True
    assert rec["excluded_listed_active"] == {"btc": 1_343, "cut": 9,
                                             "uscpi": 38}
    assert rec["excluded_listed_active_total"] == 1_390
    assert rec["excluded_total"] == 1_399
    assert rec["rule"] == POP.EXCLUDED_RULE
    for k in range(5):                       # five incremental passes later
        W.record_populate(state, dict(INCREMENTAL), full=False,
                          now=1_010.0 + 10 * k)
    assert state["populate"]["full"] is False
    assert state["populate"]["excluded"] == {"btc": 1}
    assert state["populate_full"] == rec     # untouched, pass time and all
    # the next full pass replaces it
    W.record_populate(state, dict(FULL, excluded_listed_active={"btc": 7}),
                      full=True, now=2_800.0)
    assert state["populate_full"]["at"] == 2_800.0
    assert state["populate_full"]["excluded_listed_active"] == {"btc": 7}


def test_a_pass_without_the_detail_says_it_did_not_count_it():
    rec = POP.full_pass_record({"excluded": {"btc": 2}, "full": True},
                               at=5.0)
    assert rec["excluded"] == {"btc": 2}
    assert rec["excluded_listed_active"] is None
    assert rec["excluded_listed_active_total"] is None


def test_the_heartbeat_stays_compact():
    """The heartbeat's populate section leaves both per-code maps out (they
    are in the snapshot)."""
    class _M:
        def stream_count(self):
            return 0
    d = W.heartbeat_detail(
        arming={"armed": False, "why": "T"},
        state={"populate": dict(FULL), "plan": {}, "kalshi": {}},
        plan_cfg=W.subscription_plan({}), mgr=_M(), sync={}, fresh=set())
    assert "excluded" not in d["populate"]
    assert "excluded_listed_active" not in d["populate"]
    assert d["populate"]["read"] == 74_288


def test_the_waterfall_shows_it_beside_the_sums():
    w = WF.Waterfall(now=1_800_000_000.0)
    w.add({"venue": "POLYMARKET_US", "market_type":
           "football_team_full_game_winner", "event_id": "nfl-a-b-2026"},
          {"state": "MAPPED_BUT_SETTLEMENT_NOT_PROVEN", "why": "X",
           "evidence": {"mapped": True}})
    rec = POP.full_pass_record(FULL, at=1_799_999_000.0)
    out = w.result(outside_registry=rec)
    o = out["outside_registry"]
    assert o["line"] == "NON_SPORTS_EXCLUDED_FROM_REGISTRY"
    assert o["status"] == "RECORDED" and o["in_sums"] is False
    assert o["listed_active_by_code"] == {"btc": 1_343, "cut": 9,
                                          "uscpi": 38}
    assert o["listed_active_total"] == 1_390
    assert o["full_pass_at"] == 1_799_999_000.0
    assert out["catalogue"] == 1 and out["sums_exact"] is True
    none = WF.Waterfall(now=1.0).result()["outside_registry"]
    assert none == {"line": "NON_SPORTS_EXCLUDED_FROM_REGISTRY",
                    "status": "NO_FULL_POPULATE_PASS_RECORDED",
                    "in_sums": False}


def test_the_plane_passes_the_detail_on_full_passes_and_the_record_on():
    """The worker's loop: populate(excluded_detail=full), record_populate,
    and coverage_pass(outside_registry=state["populate_full"])."""
    import inspect
    src = inspect.getsource(W.run)
    assert "excluded_detail=full" in src
    assert "record_populate(state, pop, full=full, now=now)" in src
    assert 'outside_registry=state.get("populate_full")' in src
    assert 'state["populate"] = pop' not in src


# ── 2. on Postgres: populate, the state and the snapshot ─────────────────

async def _db():
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    return c, tr


async def _premap(c, slug, *, event, sports_type="", state="PREGAME",
                  age_s=60.0):
    for side, intent in (("a", "ORDER_INTENT_BUY_LONG"),
                         ("b", "ORDER_INTENT_BUY_SHORT")):
        await c.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, "
            " kind, side_norm, intent, sports_type, listing_state, "
            " listing_state_source, updated_at, game_start) "
            "VALUES ($1,$2,$3,'side',$4,$5,$6,$7,'VENUE_LIVE_FLAG', "
            " now() - make_interval(secs => $8), "
            " now() + interval '2 hours')",
            "%s-%s" % (slug, side), event, slug, side, intent, sports_type,
            state, float(age_s))


@pg
def test_on_postgres_the_full_count_is_kept_and_published():
    async def go():
        c, tr = await _db()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            # the venue's own event-slug shapes (research-sql 37871400151)
            await _premap(c, "rc6x-nfl", event="nfl-tb-dal-2026-10-08",
                          sports_type="football_team_full_game_winner")
            await _premap(c, "rc6x-btc-1",
                          event="btc-range-hr-2026-10-09-0200z")
            await _premap(c, "rc6x-btc-2",
                          event="btc-above-2026-10-09-0300z")
            await _premap(c, "rc6x-btc-old",            # not listed now
                          event="btc-range-hr-2026-10-08-0200z",
                          age_s=5 * 3600.0)
            await _premap(c, "rc6x-cpi",
                          event="uscpi-september-mom-2026-10-14",
                          sports_type="futures")
            await _premap(c, "rc6x-cut", event="cut-2026-12-31",
                          sports_type="futures", state="ENDED")
            now = time.time()
            # off: RC5's output, key for key
            off = await POP.populate(c, since=0.0, now=now, full=True)
            assert "excluded_listed_active" not in off
            state: dict = {}
            full = await POP.populate(c, since=0.0, now=now + 1, full=True,
                                      excluded_detail=True)
            assert set(full) - set(off) == {"excluded_listed_active"}
            assert set(off) <= set(full)
            assert full["excluded"] == off["excluded"] == \
                {"btc": 3, "uscpi": 1, "cut": 1}
            assert full["excluded_listed_active"] == {"btc": 2, "uscpi": 1}
            W.record_populate(state, full, full=True, now=now + 1)
            # an incremental pass from a watermark two hours back: it reads
            # the markets changed since (not the 5 h old one) and counts
            # only those
            inc = await POP.populate(c, since=now - 7200.0, now=now + 11,
                                     full=False)
            W.record_populate(state, inc, full=False, now=now + 11)
            assert state["populate"]["full"] is False
            assert state["populate"]["excluded"] == {"btc": 2, "uscpi": 1,
                                                     "cut": 1}
            # and one that read nothing new
            inc = await POP.populate(c, since=now + 3600.0, now=now + 21,
                                     full=False)
            W.record_populate(state, inc, full=False, now=now + 21)
            assert state["populate"]["excluded"] == {}
            rec = state["populate_full"]
            assert rec["at"] == now + 1
            assert rec["excluded_listed_active"] == {"btc": 2, "uscpi": 1}
            assert rec["excluded"] == {"btc": 3, "cut": 1, "uscpi": 1}
            # none of them is a registry row
            assert await c.fetchval(
                "SELECT count(*) FROM market_plane_registry WHERE "
                " contract_id LIKE 'rc6x-%'") == 1
            # the snapshot carries it beside the last pass
            snap = await W.snapshot(c, None, dict(state, coverage={},
                                                  plan={}, catalogue={}),
                                    now=now + 12, arming={"why": "TEST"},
                                    fresh=set(), caps=(1, 10))
            assert snap["populate_full"] == rec
            assert snap["populate"]["full"] is False
            json.dumps(snap, default=str)
            # and the coverage waterfall, never in its sums
            cov = await POP.coverage_pass(
                c, now=now + 12, waterfall=True,
                outside_registry=state["populate_full"])
            o = cov["waterfall"]["outside_registry"]
            assert o["listed_active_by_code"] == {"btc": 2, "uscpi": 1}
            assert o["in_sums"] is False
            assert cov["waterfall"]["sums_exact"] is True
            assert "outside_registry" not in (
                await POP.coverage_pass(c, now=now + 12))
        finally:
            await tr.rollback()
            await c.close()
    run(go())
