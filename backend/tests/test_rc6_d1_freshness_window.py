"""THE WHOLE OBSERVATION WINDOW, UNDER A FROZEN DENOMINATOR (RC6 lane D1,
measurement; market_plane.freshness_window, the plane's freshness task,
completion.read market_data.freshness_window / pinnapi_held_inputs).

Production (research-sql run 37870039455, RC5, read only): the scorecard's
priority_members_fresh was ONE instant; over 24 h that instant ranged
0.01-1.00, its denominator 77-306 (re-chosen every pass), the plane's
snapshots arrived p50 92 s / p90 248 s apart with gaps to 1,854 s, and the
held positions' PinnAPI probability was fresh in 1 of 627 Xavier reviews --
a number nothing reported.

  §1  pure: market families, the frozen membership and its sha256, the
      venue's not-open states (the held-position rule's), one member's
      state at one instant against a REAL subscribe-all Manager
  §2  the window integral: outages count, a sample stands CARRY_S at most,
      external is excluded and counted, a sample of another membership is
      discarded, a whole-hour outage carries the last membership, time
      before measurement is never fresh nor an outage, subgroups
  §3  against Postgres: frozen once and never re-chosen (a restart re-reads
      it), one sample a minute persisted, joined members apart; the plane's
      REAL run loop samples beside the pass
  §4  the readback: market_data.freshness_window and pinnapi_held_inputs in
      the completion read, read only; the review series' missed samples
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib

import pytest

from sportsassets.market_plane import active_refresh as AR
from sportsassets.market_plane import populate as POP
from sportsassets.workers import universal_market_plane as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
HERE = pathlib.Path(__file__).resolve().parent
SLA = 300.0


def FW():
    # imported where used: a base without the module fails each test on
    # its own, not the whole file at collection
    from sportsassets.market_plane import freshness_window
    return freshness_window


def _rc6():
    spec = importlib.util.spec_from_file_location(
        "_rc6_fw_helpers", HERE / "test_rc6_priority_active_refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _rc6()
T0 = H.T0
WS = float(int(T0 // 3600) * 3600)          # the window T0 falls in


def run(coro):
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════
# §1 pure
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mt,fa,want", [
    ("football_team_full_game_winner", "WINNER", "MONEYLINE"),
    ("soccer_team_full_time_winner", "WINNER", "MONEYLINE"),
    ("baseball_team_full_game_winner", None, "MONEYLINE"),
    ("football_team_full_game_spread", "MARGIN", "SPREAD"),
    ("tennis_match_games_spread", None, "SPREAD"),
    ("football_team_full_game_total", "POINTS", "TOTAL"),
    ("tennis_match_total_games", None, "TOTAL"),
    ("football_team_points_full_game_total", "TEAM_SCORE", "TEAM_TOTAL"),
    ("hockey_team_total_goals", None, "TEAM_TOTAL"),
    ("baseball_team_total_runs", None, "TEAM_TOTAL"),
    ("futures", "CHAMPION", "FUTURE"),
    (None, "AWARD_WINNER", "FUTURE"),
    ("soccer_game_total_corners", "TOTAL_CORNERS", "TOTAL"),
    ("football_player_receiving_yards", "RECEIVING_YARDS", "OTHER"),
    (None, None, "UNKNOWN"),
])
def test_market_families_from_the_venue_type_and_the_ontology(mt, fa, want):
    assert FW().family_of(mt, fa) == want


def _m(cid, tier="CANDIDATE", **kw):
    m = {"contract_id": cid, "tier": tier, "venue": "POLYMARKET_US",
         "family": "MONEYLINE", "period": "FULL_EVENT",
         "event_start": T0 + 3600, "event_id": "ev-" + cid, "line": None,
         "orders": False}
    m.update(kw)
    return m


def test_the_frozen_membership_is_sorted_hashed_and_order_free():
    F = FW()
    ms = [_m("b"), _m("a", tier="HELD_POSITION"), _m("c", tier="WORKING_ORDER",
                                                     orders=True)]
    w1 = F.freeze(ms, window_start=WS, now=T0, sla_s=SLA)
    w2 = F.freeze(list(reversed(ms)), window_start=WS, now=T0 + 5, sla_s=SLA)
    assert [r[0] for r in w1["members"]] == ["a", "b", "c"]
    assert w1["membership_hash"] == w2["membership_hash"]
    assert w1["n"] == 3 and len(w1["membership_hash"]) == 64
    # a member changing tier is another membership
    w3 = F.freeze([_m("b", tier="HELD_POSITION")] + ms[1:],
                  window_start=WS, now=T0, sla_s=SLA)
    assert w3["membership_hash"] != w1["membership_hash"]
    d = F.member_dicts(w1)
    assert d[2]["working_order"] == 1 and d[0]["tier"] == "HELD_POSITION"


def test_alternative_lines_are_every_line_of_a_multi_line_group():
    F = FW()
    ms = [_m("s1", family="SPREAD", event_id="E", line="-3.5"),
          _m("s2", family="SPREAD", event_id="E", line="-6.5"),
          _m("t1", family="TOTAL", event_id="E", line="44.5"),
          _m("s3", family="SPREAD", event_id="F", line="-1.5"),
          _m("ml", family="MONEYLINE", event_id="E")]
    w = F.freeze(ms, window_start=WS, now=T0, sla_s=SLA)
    alt = {r[0]: r[6] for r in w["members"]}
    assert alt == {"ml": 0, "s1": 1, "s2": 1, "s3": 0, "t1": 0}


def test_the_not_open_states_are_the_held_position_rules_and_order_states():
    from sportsassets import bettor_paper_freshness as PF
    from sportsassets import bettor_paper_ledger as L
    F = FW()
    assert PF.TERMINAL_MARKET_STATES <= F.TERMINAL_STATES
    assert PF.TRANSIENT_NOT_OPEN_STATES <= F.TRANSIENT_STATES
    assert tuple(L.OPEN_STATES) == F.OPEN_ORDER_STATES
    assert not (F.TERMINAL_STATES & F.TRANSIENT_STATES)
    assert "INSTRUMENT_STATE_OPEN" not in F.TERMINAL_STATES | \
        F.TRANSIENT_STATES
    assert F.REFRESH_NOT_OPEN == AR.R_REFRESH_NOT_OPEN


def _plane_at(syms, *, ages, closed=()):
    """A REAL subscribe-all Manager: each symbol's last update `ages[s]`
    seconds before T0 + 1000; the connection alive at that instant."""
    clock = H.Clock(T0)
    m, books = H.plane(clock, syms)
    now = T0 + 1000.0
    for s in syms:
        if ages.get(s) is not None:
            clock.t = now - ages[s]
            H.update(books, s, clock.t,
                     state=("INSTRUMENT_STATE_CLOSED" if s in closed
                            else H.OPEN))
    clock.t = now
    books.on_heartbeat()
    return m, now


def test_one_member_at_one_instant_names_its_source_and_its_instants():
    F = FW()
    syms = ["s-stream", "s-refresh", "s-paper", "s-quiet", "s-closed"]
    m, now = _plane_at(syms, ages={"s-stream": 12.0, "s-refresh": 900.0,
                                   "s-paper": 900.0, "s-quiet": 900.0,
                                   "s-closed": 5000.0},
                       closed=("s-closed",))
    refreshed = {"s-refresh": (now - 40.0, "R")}
    entries = {"s-refresh": {"venue_ts": now - 3000.0}}
    paper = {"s-paper": {"at": now - 100.0, "venue_ts": "2026-10-09T00:00:00Z",
                         "market_state": "MARKET_STATE_OPEN"}}
    got = {s: F.classify(F.member_dicts(F.freeze(
        [_m(s)], window_start=WS, now=now, sla_s=SLA))[0], mgr=m,
        refreshed=refreshed, entries=entries, paper=paper, kalshi={},
        now=now, sla_s=SLA) for s in syms + ["not-held"]}
    code, why, rcv, src = got["s-stream"]
    assert code == "S" and abs(now - rcv - 12.0) < 1e-6
    assert src is not None and abs(now - src - 12.0) < 1e-3
    assert got["s-refresh"][0] == "R" and got["s-refresh"][2] == now - 40.0
    assert got["s-refresh"][3] == now - 3000.0      # source kept apart
    assert got["s-paper"][0] == "P" and got["s-paper"][2] == now - 100.0
    assert got["s-quiet"][0] == "N"
    assert got["s-quiet"][1].startswith("STREAM:SNAPSHOT_OLDER_THAN_THE_BOUND")
    # the venue's own terminal state on the stream: external, any age
    assert got["s-closed"][0] == "X"
    assert got["s-closed"][1] == ("STREAM:MARKET_STATE_TERMINAL:"
                                  "INSTRUMENT_STATE_CLOSED")
    assert got["not-held"][0] == "U"


def test_external_needs_the_venues_own_word_terminal_or_inside_the_bound():
    F = FW()
    now = T0
    assert F.external_from("MARKET_STATE_SETTLED", read_at=now - 9e5,
                           now=now, sla_s=SLA, source="P")[0]
    assert F.external_from("MARKET_STATE_HALTED", read_at=now - 30,
                           now=now, sla_s=SLA, source="P")[0]
    # a halt read 10 minutes ago says nothing about now
    assert not F.external_from("MARKET_STATE_HALTED", read_at=now - 600,
                               now=now, sla_s=SLA, source="P")[0]
    assert not F.external_from("MARKET_STATE_OPEN", read_at=now, now=now,
                               sla_s=SLA, source="P")[0]
    assert not F.external_from(None, read_at=now, now=now, sla_s=SLA,
                               source="P")[0]
    # the plane's REST read said not open inside the bound: external
    mem = F.member_dicts(F.freeze([_m("x")], window_start=WS, now=now,
                                  sla_s=SLA))[0]
    got = F.classify(mem, mgr=None, refreshed={}, entries={"x": {
        "outcome": AR.R_REFRESH_NOT_OPEN, "tried_at": now - 60}},
        paper={}, kalshi={}, now=now, sla_s=SLA)
    assert got[0] == "X" and got[1] == "REFRESH:MARKET_NOT_OPEN"
    got = F.classify(mem, mgr=None, refreshed={}, entries={"x": {
        "outcome": AR.R_REFRESH_NOT_OPEN, "tried_at": now - 400}},
        paper={}, kalshi={}, now=now, sla_s=SLA)
    assert got[0] == "U"
    # a paper read inside the bound of a CLOSED market is not a current
    # book: the held-position rule judges the state first
    got = F.classify(mem, mgr=None, refreshed={}, entries={}, paper={
        "x": {"at": now - 20, "venue_ts": None,
              "market_state": "MARKET_STATE_CLOSED"}}, kalshi={},
        now=now, sla_s=SLA)
    assert got[:2] == ("X", "PAPER_REST:MARKET_STATE_TERMINAL:"
                            "MARKET_STATE_CLOSED")


def test_a_kalshi_member_is_current_only_with_a_readable_book_in_the_bound():
    F = FW()
    mem = F.member_dicts(F.freeze([_m("KXT-1", venue="KALSHI")],
                                  window_start=WS, now=T0, sla_s=SLA))[0]
    k = {"KXT-1": {"readable": True, "error": None, "observed_at": T0 - 20}}
    assert F.classify(mem, mgr=None, refreshed={}, entries={}, paper={},
                      kalshi=k, now=T0, sla_s=SLA)[0] == "K"
    k["KXT-1"]["observed_at"] = T0 - 301
    assert F.classify(mem, mgr=None, refreshed={}, entries={}, paper={},
                      kalshi=k, now=T0, sla_s=SLA)[:2] == (
        "N", "KALSHI:BOOK_OLDER_THAN_THE_BOUND")
    assert F.classify(mem, mgr=None, refreshed={}, entries={}, paper={},
                      kalshi={}, now=T0, sla_s=SLA)[:2] == (
        "N", "KALSHI:NO_BOOK_HELD")


# ═════════════════════════════════════════════════════════════════════
# §2 the window integral
# ═════════════════════════════════════════════════════════════════════

def _win(ws, members, frozen_at=None):
    return FW().freeze(members, window_start=ws, now=frozen_at or ws + 5,
                       sla_s=SLA)


def _smp(win, at, codes):
    return {"window_start": win["window_start"],
            "membership_hash": win["membership_hash"], "verified_at": at,
            "codes": codes, "n": win["n"]}


def test_every_minute_fresh_is_one_and_an_unsampled_half_hour_is_an_outage():
    F = FW()
    w = _win(WS, [_m("a"), _m("b")], frozen_at=WS)
    full = [_smp(w, WS + 60 * i, "SS") for i in range(60)]
    got = F.integrate([w], full, start=WS, end=WS + 3600)
    assert got["groups"]["ALL"]["rate"] == 1.0
    assert got["outage_s"] == 0.0
    # the plane stops sampling after 30 minutes: the rest is OUTAGE, every
    # frozen member eligible and not fresh -- never dropped
    half = full[:30]
    got = F.integrate([w], half, start=WS, end=WS + 3600)
    g = got["groups"]["ALL"]
    # the last sample at 29:00 stands CARRY_S (2 minutes), to 31:00
    assert g["fresh_member_s"] == 2 * 31 * 60
    assert g["eligible_member_s"] == 2 * 3600
    assert g["outage_member_s"] == 2 * 29 * 60
    assert got["outage_s"] == 29 * 60
    assert g["rate"] == round(31 / 60, 4)
    assert got["longest_outages"][0]["s"] == 29 * 60


def test_external_is_excluded_from_eligible_and_counted():
    F = FW()
    w = _win(WS, [_m("a"), _m("b"), _m("c")], frozen_at=WS)
    s = [_smp(w, WS + 60 * i, "SXN") for i in range(60)]
    g = F.integrate([w], s, start=WS, end=WS + 3600)["groups"]["ALL"]
    assert g["eligible_member_s"] == 2 * 3600
    assert g["external_member_s"] == 3600
    assert g["rate"] == 0.5
    assert g["rate_external_counted_not_fresh"] == round(1 / 3, 4)


def test_a_sample_of_another_membership_is_discarded_and_its_time_is_outage():
    F = FW()
    w = _win(WS, [_m("a"), _m("b")], frozen_at=WS)
    good = [_smp(w, WS + 60 * i, "SS") for i in range(60)]
    forged = dict(good[30], membership_hash="0" * 64)
    short = dict(good[31], codes="S")
    got = F.integrate([w], good[:30] + [forged, short] + good[32:],
                      start=WS, end=WS + 3600)
    assert got["discarded_samples_n"] == 2
    # 29:00 carries two minutes to 31:00; 32:00 resumes: one minute out
    assert got["outage_s"] == 60.0
    assert got["groups"]["ALL"]["rate"] == round(59 / 60, 4)


def test_a_sample_carries_across_the_hour_and_a_lost_hour_carries_members():
    F = FW()
    w0 = _win(WS, [_m("a")], frozen_at=WS)
    w1 = _win(WS + 3600, [_m("a"), _m("b")])                 # frozen 01:00:05
    s = [_smp(w0, WS + 60 * i + 30, "S") for i in range(60)]  # :30 each
    s += [_smp(w1, WS + 3600 + 30 + 60 * i, "SN") for i in range(60)]
    got = F.integrate([w0, w1], s, start=WS, end=WS + 7200)
    # 00:59:30's sample stands to 01:00:30 (no boundary outage); only the
    # first 30 s of the horizon (before the first sample) is outage
    assert got["outage_s"] == 30.0
    a = got["groups"]["ALL"]
    # a: fresh 00:00:30-01:00:30 (w0) and 01:00:30-02:00:00 (w1); b (w1
    # only) never; the first 30 s an outage of w0's one member
    assert a["fresh_member_s"] == 3600.0 + 3570.0
    assert a["eligible_member_s"] == 30.0 + 3600.0 + 2 * 3570.0
    assert a["rate"] == round(7170 / 10770, 4)
    # an hour with no window event at all: the last membership is carried
    s2 = [x for x in s if x["window_start"] == WS]
    got = F.integrate([w0], s2, start=WS, end=WS + 7200)
    assert got["carried_windows"] == [WS]
    assert got["groups"]["ALL"]["outage_member_s"] == pytest.approx(
        30.0 + 3600.0 - 90.0, abs=1e-6)


def test_time_before_the_first_frozen_window_is_never_fresh_nor_outage():
    F = FW()
    w = _win(WS, [_m("a")], frozen_at=WS + 1800)
    s = [_smp(w, WS + 1800 + 60 * i, "S") for i in range(30)]
    got = F.integrate([w], s, start=WS - 7200, end=WS + 3600)
    assert got["measured_from"] == WS + 1800
    assert got["observation_s"] == 1800.0
    assert got["groups"]["ALL"]["rate"] == 1.0
    none = F.integrate([], [], start=WS, end=WS + 3600)
    assert none["status"] == "UNMEASURED" and none["groups"] == {}


def test_the_subgroups_tier_management_view_family_phase_and_alt_lines():
    F = FW()
    ms = [_m("held", tier="HELD_POSITION"),
          _m("order", tier="WORKING_ORDER", orders=True, family="TOTAL",
             event_id="E", line="44.5"),
          _m("cand-a", family="TOTAL", event_id="E", line="47.5"),
          _m("cand-b", event_start=WS - 7 * 3600)]
    w = _win(WS, ms, frozen_at=WS)
    order = [r[0] for r in w["members"]]
    codes = {"held": "S", "order": "R", "cand-a": "N", "cand-b": "N"}
    s = [_smp(w, WS + 60 * i, "".join(codes[c] for c in order))
         for i in range(60)]
    g = F.integrate([w], s, start=WS, end=WS + 3600)["groups"]
    assert g["tier:HELD_POSITION"]["rate"] == 1.0
    assert g["tier:WORKING_ORDER"]["rate"] == 1.0
    assert g["view:HELD_POSITION_OR_WORKING_ORDER"]["rate"] == 1.0
    assert g["tier:CANDIDATE"]["rate"] == 0.0
    assert g["family:TOTAL"]["rate"] == 0.5
    assert g["family:ALTERNATIVE_LINE"]["rate"] == 0.5
    assert g["phase:STARTED_GT_4H"]["rate"] == 0.0
    assert g["phase:PREGAME"]["rate"] == round(2 / 3, 4)
    assert g["venue:POLYMARKET_US"]["rate"] == 0.5
    assert g["ALL"]["rate"] == 0.5


def test_the_readback_declares_every_subgroup_and_names_the_three_instants():
    F = FW()
    w = _win(WS, [_m("a")], frozen_at=WS)
    s = [dict(_smp(w, WS + 60 * i, "S"),
              times={"verified_at": WS + 60 * i,
                     "receipt_age_s": {"n": 1, "p50": 3.0, "p90": 3.0,
                                       "max": 3.0},
                     "source_event_age_s": None},
              joined={"n": 1, "codes": "N", "ids": ["late"],
                      "tiers": ["CANDIDATE"]})
         for i in range(60)]
    out = F.readback([json.dumps(w)], [json.dumps(x) for x in s],
                     now=WS + 3600)
    h = out["horizons"]["1h"]
    assert h["status"] == "MEASURED" and h["groups"]["ALL"]["rate"] == 1.0
    dg = h["declared_groups"]
    assert dg["tier:CANDIDATE"] == "MEASURED"
    for k in ("tier:HELD_POSITION", "tier:WORKING_ORDER", "venue:KALSHI",
              "family:SPREAD", "family:FUTURE", "family:ALTERNATIVE_LINE"):
        assert dg[k] == "NOT_IN_ELIGIBLE_SET", k
    ls = out["latest_sample"]
    assert ls["verified_at"] == WS + 3540 and ls["age_s"] == 60.0
    assert ls["times"]["receipt_age_s"]["p50"] == 3.0
    assert ls["joined"]["n"] == 1 and ls["joined"]["current"] == 0
    assert out["windows"][0]["membership_hash"] == w["membership_hash"]


# ═════════════════════════════════════════════════════════════════════
# §3 against Postgres
# ═════════════════════════════════════════════════════════════════════

class _OnePool:
    def __init__(self, c):
        self.c, self.lock = c, asyncio.Lock()

    def acquire(self):
        pool = self

        class A:
            async def __aenter__(self_):
                await pool.lock.acquire()
                return pool.c

            async def __aexit__(self_, *a):
                pool.lock.release()
                return False
        return A()


async def _seed(c, rows):
    """Inside the test's rolled-back transaction: only these registry rows
    active, and no other test's working PAPER order left open (each would be
    an eligible WORKING_ORDER member here)."""
    await c.execute("UPDATE market_plane_registry SET active=false")
    await c.execute(
        "UPDATE paper_orders SET state = 'CANCELED', terminal_at = now(), "
        " terminal_reason = 'TEST_ISOLATION' WHERE state = ANY($1::text[])",
        list(FW().OPEN_ORDER_STATES))
    for cid, prio, mt, fam in rows:
        await c.execute(
            "INSERT INTO market_plane_registry (contract_id, venue, active, "
            " desired_subscription, updated_at, priority, required_reason, "
            " market_type, family, period, event_id, event_start, "
            " last_seen_at) VALUES ($1, 'POLYMARKET_US', true, true, now(),"
            " $2, 'EVALUATED_CANDIDATE', $3, $4, 'FULL_EVENT', 'ev-1', "
            " now() + interval '3 hours', now()) ON CONFLICT (contract_id) "
            " DO UPDATE SET active = true, priority = excluded.priority, "
            " market_type = excluded.market_type, family = excluded.family",
            cid, prio, mt, fam)


@pg
def test_the_membership_is_frozen_once_and_never_re_chosen():
    import asyncpg
    F = FW()

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("DELETE FROM market_plane_events "
                            " WHERE kind IN ('FRESHNESS_WINDOW', "
                            "                'FRESHNESS_SAMPLE')")
        except Exception:                                       # noqa: BLE001
            pass
        try:
            await _seed(c, [("fw-held", 0, "football_team_full_game_winner",
                             "WINNER"),
                            ("fw-cand", 10, "football_team_full_game_total",
                             "POINTS")])
            now = T0 + 120.0
            w1 = await F.load_or_freeze(c, now=now, sla_s=SLA)
            # the eligible set changes inside the window ...
            await _seed(c, [("fw-new", 10, "futures", "CHAMPION")])
            w2 = await F.load_or_freeze(c, now=now + 600, sla_s=SLA)
            # ... a restart re-reads the window's membership, unchanged
            w3 = await F.load_or_freeze(c, now=now + 1200, sla_s=SLA)
            nxt = await F.load_or_freeze(c, now=WS + 3600 + 10, sla_s=SLA)
            return w1, w2, w3, nxt
        finally:
            await tr.rollback()
            await c.close()
    w1, w2, w3, nxt = run(go())
    ids = [r[0] for r in w1["members"]]
    assert "fw-held" in ids and "fw-cand" in ids and "fw-new" not in ids
    tiers = {r[0]: r[1] for r in w1["members"]}
    assert tiers["fw-held"] == "HELD_POSITION" and tiers["fw-cand"] == \
        "CANDIDATE"
    fam = {r[0]: r[3] for r in w1["members"]}
    assert fam["fw-cand"] == "TOTAL"
    assert w1["loaded"] is False and w2["loaded"] is True
    assert w2["membership_hash"] == w1["membership_hash"] == \
        w3["membership_hash"]
    assert w2["members"] == w1["members"]
    # the next hour freezes its own, with the newcomer
    assert nxt["window_start"] == WS + 3600 and nxt["loaded"] is False
    assert "fw-new" in [r[0] for r in nxt["members"]]


@pg
def test_one_sample_a_minute_is_persisted_with_joined_members_apart():
    import asyncpg
    F = FW()

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await _seed(c, [("fs-a", 10, "football_team_full_game_winner",
                             "WINNER"),
                            ("fs-b", 10, "football_team_full_game_winner",
                             "WINNER")])
            # the paper runtime read fs-a 100 s ago: current through it
            await c.execute(
                "INSERT INTO paper_book_observations (us_market_slug, "
                " observed_at, source, read_basis, market_state) VALUES "
                " ('fs-a', to_timestamp($1), 'REST', 'TEST', "
                " 'MARKET_STATE_OPEN')", T0 + 20.0)
            pool = _OnePool(c)
            st: dict = {}
            got1 = await F.step(pool, None, None, st, now=T0 + 120.0,
                                sla_s=SLA)
            assert await F.step(pool, None, None, st, now=T0 + 150.0,
                                sla_s=SLA) is None          # not due yet
            await _seed(c, [("fs-late", 10, "soccer_team_full_time_winner",
                             "WINNER")])
            got2 = await F.step(pool, None, None, st, now=T0 + 181.0,
                                sla_s=SLA)
            rows = [json.loads(r["payload"]) for r in await c.fetch(
                "SELECT payload FROM market_plane_events WHERE kind = "
                " 'FRESHNESS_SAMPLE' ORDER BY at")]
            return got1, got2, rows
        finally:
            await tr.rollback()
            await c.close()
    got1, got2, rows = run(go())
    assert got1["n"] == 2 and got1["current"] == 1
    assert len(rows) == 2
    r1, r2 = rows
    assert r1["membership_hash"] == r2["membership_hash"]
    # fs-a (P, the paper read) and fs-b (U: no plane books here), in order
    assert r1["codes"] == "PU"
    assert r1["times"]["verified_at"] == T0 + 120.0
    assert r1["times"]["receipt_age_s"]["max"] == 100.0
    assert r2["joined"]["n"] == 1 and r2["joined"]["ids"] == ["fs-late"]
    assert r2["n"] == 2                                  # frozen, not 3
    assert got2["joined"] == 1


@pg
def test_the_plane_run_loop_samples_beside_the_pass(monkeypatch):
    """The plane's REAL run loop (stream not armed: no credential) on a
    rolled-back connection: the freshness task freezes the window and
    persists a sample, and the heartbeat carries the sample's counts."""
    import asyncpg
    beats = []

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        pool = _OnePool(c)

        async def get_pool():
            return pool

        async def hb(service, status="ok", detail=None, con=None):
            beats.append((service, status, detail))
            if len(beats) >= 2:
                raise asyncio.CancelledError()
        monkeypatch.setattr(W, "get_pool", get_pool)
        monkeypatch.setattr(W, "heartbeat", hb)
        monkeypatch.setattr(W, "INTERVAL_S", 0.3)
        monkeypatch.setenv("INSTITUTIONAL_MD_STREAM", "off")
        monkeypatch.setenv("KALSHI_CATALOGUE", "off")
        try:
            await c.execute("DELETE FROM us_premap")
            await _seed(c, [("rl-a", 10, "football_team_full_game_winner",
                             "WINNER")])

            async def required(conn):
                return set(), {"rl-a"}, True
            monkeypatch.setattr(POP, "required_sets_read", required)
            with pytest.raises(asyncio.CancelledError):
                await W.run()
            return [json.loads(r["payload"]) for r in await c.fetch(
                "SELECT payload FROM market_plane_events WHERE kind IN "
                " ('FRESHNESS_WINDOW', 'FRESHNESS_SAMPLE') ORDER BY at")]
        finally:
            await tr.rollback()
            await c.close()
    rows = run(go())
    kinds = [("members" in r) for r in rows]
    assert kinds[0] is True and False in kinds          # window, then sample
    smp = [r for r in rows if "codes" in r][0]
    assert smp["n"] >= 1 and set(smp["codes"]) <= {"U", "P", "N"}
    fw = beats[-1][2]["freshness_window"]
    assert fw["n"] == smp["n"] and fw["membership_hash"] == \
        smp["membership_hash"]


# ═════════════════════════════════════════════════════════════════════
# §4 the readback
# ═════════════════════════════════════════════════════════════════════

def test_the_held_inputs_series_counts_a_missed_review_as_not_fresh():
    from sportsassets.completion import read as CR
    now = T0
    rows = []
    # g1: open, reviewed every 100 s for an hour, ONE fresh review
    for i in range(36):
        rows.append({"group_id": "g1", "at": now - 3550 + 100 * i,
                     "state": ("FRESH_CURRENT_PROBABILITY" if i == 7
                               else "STALE_ENTRY_TIME_PROBABILITY"),
                     "source": "PINNACLE_ONLY_LATEST", "p_at": None,
                     "p_rcv": None, "p_limit": "30"})
    # g2: open now, never reviewed in the horizon: every review it owed is
    # missed
    got = CR.pinnapi_inputs_summary(
        rows, {"g1": {"slug": "s1"}, "g2": {"slug": "s2"}}, now=now,
        horizon_s=3600.0)
    assert got["reviews"] == 36 and got["fresh_reviews"] == 1
    # g1 owes 3550 // 300 + 1 = 12 (it has 36); g2 owes 13 and has 0
    assert got["missed_reviews"] == 13
    assert got["denominator"] == 49 and got["rate"] == round(1 / 49, 4)
    assert got["rate_reviews_only"] == round(1 / 36, 4)
    g1 = [g for g in got["per_group"] if g["group_id"] == "g1"][0]
    assert g1["last"]["verified_at"] == now - 50
    assert g1["last"]["evidence_state"] == "STALE_ENTRY_TIME_PROBABILITY"


@pg
def test_the_completion_read_carries_the_window_and_the_held_inputs():
    import asyncpg
    from sportsassets.completion import read as CR

    async def go():
        c = await asyncpg.connect(DSN)
        try:
            before = await c.fetchval(
                "SELECT sum(n_tup_ins + n_tup_upd + n_tup_del) "
                "  FROM pg_stat_user_tables")
            async with c.transaction(readonly=True):
                await c.execute("SET LOCAL statement_timeout = 60000")
                got = await CR.read(c)
            after = await c.fetchval(
                "SELECT sum(n_tup_ins + n_tup_upd + n_tup_del) "
                "  FROM pg_stat_user_tables")
            return got, before, after
        finally:
            await c.close()
    got, before, after = run(go())
    assert before == after
    md = got["market_data"]
    fw = md["freshness_window"]
    assert set(fw["horizons"]) == {"1h", "6h", "24h"}
    assert fw["status"] in ("MEASURED", "UNMEASURED")
    assert "working_orders_now" in fw and fw["carry_s"] == 120.0
    pin = md["pinnapi_held_inputs"]
    assert pin["source"] == "paper_xavier_reviews.measure.evidence_state"
    assert set(pin["horizons"]) == {"6h", "24h"}
    assert md["priority_freshness"]["measure"] == "ONE_INSTANT"
    t = got["section_timings"]
    assert t["freshness_window"]["ok"] and t["pinnapi_held_inputs"]["ok"]
