"""XAVIER'S FRESHNESS, MEASURED WITHOUT CONFLATING TWO THINGS.

A SCHEDULED_BACKSTOP review of a position whose provider quote has not
changed is correctly STALE_ENTRY_TIME_PROBABILITY; it is counted as such
(`scheduled_review_no_provider_change`, informational) and excluded from
freshness quality. Freshness quality is MARKET_EVENT reviews on a fresh
probability; the SLA is a MARKET_EVENT review within 30 s of the change
(median and p90 reported); monitoring is the open positions with a review
inside their due bound (management_view); discretion is on fresh evidence
only (100 % by rule). Unmeasured is UNAVAILABLE with a null value, never 0.
Measurement only: the scorecard writes nothing and relabels nothing.

ALL DATA IS SYNTHETIC TEST DATA, inside a transaction that is rolled back.
"""
from __future__ import annotations

import inspect
import re

import asyncpg
import pytest

from sportsassets.agents import quality_scorecard as Q
from sportsassets.agents import xavier_management as XM
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
#: a synthetic clock far from any real row, so the windows hold only ours
NOW = 4102444800.0          # 2100-01-01
KEYS = {"id", "name", "value", "unit", "numerator", "denominator", "sample",
        "min_sample", "ci", "trend", "last_measured_at", "status", "why",
        "blocker", "next_improvement", "detail"}


def _row(trigger, ev, n, *, kind="PAPER", rec=False, permitted=None):
    return {"kind": kind, "trigger": trigger, "ev": ev, "rec_disc": rec,
            "permitted": (ev == Q.FRESH) if permitted is None else permitted,
            "n": n, "last": NOW - 10}


# ═════════════════════════════════════════════════════════════════════
# PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_split_separates_market_changes_from_unchanged_quotes():
    rows = [_row(Q.T_MARKET, Q.FRESH, 9), _row(Q.T_MARKET, Q.STALE, 1),
            _row(Q.T_BACKSTOP, Q.STALE, 80), _row(Q.T_BACKSTOP, Q.UNAVAIL, 5),
            _row(Q.T_BACKSTOP, Q.FRESH, 3), _row("FIRST_FILL", Q.FRESH, 2),
            _row(Q.T_MARKET, Q.FRESH, 4, kind="ACTUAL", rec=True)]
    s = Q.freshness_split(rows)
    assert s["total"] == 104
    assert (s["market_fresh"], s["market_n"]) == (13, 14)
    assert s["market_by_kind"] == {"PAPER": {"n": 10, "fresh": 9},
                                   "ACTUAL": {"n": 4, "fresh": 4}}
    # unchanged quotes: backstop reviews NOT on a fresh probability
    assert s["no_change"] == 85
    assert (s["backstop_n"], s["backstop_fresh"]) == (88, 3)
    assert s["quality_denominator"] == 104 - 85
    assert s["by_trigger"][Q.T_BACKSTOP] == {"n": 88, "fresh": 3}
    # discretion: recommended or permitted
    assert (s["rec_n"], s["rec_fresh"]) == (4, 4)
    assert s["disc_n"] == s["permitted_n"] == 18 and s["disc_fresh"] == 18


def test_the_old_all_reviews_share_and_the_quality_share_differ():
    """The production shape: few fresh among all, all fresh on changes."""
    rows = [_row(Q.T_MARKET, Q.FRESH, 377),
            _row(Q.T_BACKSTOP, Q.STALE, 29312)]
    s = Q.freshness_split(rows)
    assert round(s["fresh_all"] / s["total"], 4) == 0.0127
    assert s["market_fresh"] / s["market_n"] == 1.0


def test_sla_split_median_and_p90():
    lats = [0.2, 0.4, 0.5, 0.9, 1.0, 2.0, 3.0, 5.0, 31.0, 120.0]
    s = Q.sla_split(lats)
    assert (s["n"], s["within"]) == (10, 8)
    assert s["median"] == 1.5 and s["p90"] == 31.0 and s["max"] == 120.0
    assert Q.sla_split([]) == {"n": 0, "within": 0, "median": None,
                               "p90": None, "max": None}
    assert Q.MARKET_CHANGE_REVIEW_SLA_S == 30.0


def test_monitored_split_counts_open_positions_only():
    pos = [
        {"position_kind": "PAPER", "state": "OPEN",
         "latest_review": {"at": 1}, "review_overdue": False},
        {"position_kind": "PAPER", "state": "OPEN",
         "latest_review": {"at": 1}, "review_overdue": True},
        {"position_kind": "PAPER", "state": "OPEN", "latest_review": None,
         "review_overdue": None},
        {"position_kind": "ACTUAL", "state": "OPEN",
         "latest_review": {"at": 1}, "review_overdue": False},
        {"position_kind": "PAPER", "state": "CLOSED", "latest_review": None,
         "review_overdue": None}]
    s = Q.monitored_split(pos)
    assert (s["open"], s["monitored"], s["overdue"], s["without_review"]) \
        == (4, 2, 1, 1)
    assert s["by_kind"]["ACTUAL"] == {"open": 1, "monitored": 1,
                                      "overdue": 0, "without_review": 0}


def test_the_scorecard_writes_nothing_and_never_restamps_a_quote():
    src = inspect.getsource(Q)
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ",
                 "TRUNCATE "):
        assert verb not in re.sub(r'"""[\s\S]*?"""', "", code), verb
    # the measurement bound is not a decision input: nothing that decides
    # imports the scorecard
    for mod in ("paper_xavier", "xavier_management"):
        m = __import__("sportsassets.agents.%s" % mod, fromlist=["x"])
        assert "quality_scorecard" not in inspect.getsource(m), mod


# ═════════════════════════════════════════════════════════════════════
# DATABASE
# ═════════════════════════════════════════════════════════════════════

async def _assess(conn, i, *, trigger, ev, at, due_at=None, kind="PAPER",
                  rec="HOLD"):
    lat = XM.latency(kind=kind, trigger=trigger, at=at, due_at=due_at,
                     cadence_s=60.0)
    evidence = {"evidence_state": ev,
                "probability": None if ev == Q.UNAVAIL else 0.55,
                "probability_source": "TEST",
                "probability_age_s": 0.5 if ev == Q.FRESH else 500.0}
    a = XM.assessment(
        kind=kind, group_id="xfsc-g%d" % i, review_id="xfsc-r%d" % i,
        thesis=None, at=at, lat=lat, evidence=evidence, venue_economics={},
        thesis_state={"state": XM.TH_NONE}, alternatives=[],
        recommendation=rec, reallocate={"mode": "SHADOW"},
        policy={"status": "READY_FOR_OWNER_APPROVAL"})
    got = await XM.record_assessment(conn, a)
    assert got["ok"], got


def _by_id(ms):
    return {m["id"]: m for m in ms}


@pg
async def test_unmeasured_is_unavailable_never_zero():
    conn = await asyncpg.connect(H.DSN)
    try:
        ms = _by_id(await Q.freshness_metrics(conn, NOW))
        assert set(ms) == set(Q.FRESHNESS_IDS)
        for m in ms.values():
            assert set(m) == KEYS
            assert m["status"] == "UNAVAILABLE" and m["value"] is None, m
            assert m["why"], m
    finally:
        await conn.close()


@pg
async def test_the_freshness_metrics_from_assessments():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        i = 0
        # 40 market-change reviews: 38 fresh, latency 0.2..; 2 stale late
        for j in range(38):
            i += 1
            at = NOW - 3600 - j
            await _assess(conn, i, trigger=Q.T_MARKET, ev=Q.FRESH, at=at,
                          due_at=at - (0.2 + 0.02 * j),
                          rec="EXIT" if j < 3 else "HOLD")
        for j in range(2):
            i += 1
            at = NOW - 7200 - j
            await _assess(conn, i, trigger=Q.T_MARKET, ev=Q.STALE, at=at,
                          due_at=at - 45.0)
        # 60 scheduled reviews of unchanged quotes: correctly stale
        for j in range(60):
            i += 1
            await _assess(conn, i, trigger=Q.T_BACKSTOP,
                          ev=Q.STALE if j % 10 else Q.UNAVAIL,
                          at=NOW - 100 - j, due_at=NOW - 160 - j)
        # one scheduled review that happened to be fresh: not "no change"
        i += 1
        await _assess(conn, i, trigger=Q.T_BACKSTOP, ev=Q.FRESH,
                      at=NOW - 50, due_at=NOW - 110)
        # an actual market-change review with no change instant
        i += 1
        await _assess(conn, i, trigger=Q.T_MARKET, ev=Q.FRESH, at=NOW - 40,
                      kind="ACTUAL")
        ms = _by_id(await Q.freshness_metrics(conn, NOW))

        f = ms["freshness_when_market_changed"]
        assert (f["numerator"], f["denominator"]) == (39, 41)
        assert f["value"] == round(39 / 41, 6) and f["status"] == "MEASURED"
        assert f["ci"]["method"] == "WILSON"
        assert f["detail"]["by_kind"]["ACTUAL"] == {"n": 1, "fresh": 1}
        assert "2 of 41" in f["blocker"]

        s = ms["scheduled_review_no_provider_change"]
        assert s["numerator"] == 60 and s["denominator"] == 102
        assert s["detail"]["informational"] is True
        assert s["detail"]["backstop_total"] == 61
        assert s["detail"]["backstop_fresh"] == 1
        assert s["detail"]["backstop_unavailable"] == 6
        assert s["detail"]["freshness_quality_denominator"] == 42
        assert s["detail"]["fresh_share_excluding_unchanged_quotes"] == \
            round(40 / 42, 6)
        assert s["blocker"] is None

        sla = ms["market_change_to_review_sla"]
        assert (sla["numerator"], sla["denominator"]) == (38, 40)
        assert sla["detail"][
            "market_event_reviews_without_a_change_instant"] == 1
        assert sla["detail"]["median_s"] == pytest.approx(0.59, abs=0.02)
        assert sla["detail"]["p90_s"] == pytest.approx(0.9, abs=0.05)
        assert sla["detail"]["max_s"] == 45.0
        assert "not persisted per event" in sla["blocker"]
        assert sla["blocker"].startswith("2 of 40 reviewed later than 30 s")

        lat = ms["market_change_review_latency_s"]
        assert lat["value"] == sla["detail"]["median_s"]
        assert lat["unit"] == "s" and lat["ci"]["method"] == \
            "ORDER_STATISTICS"

        d = ms["freshness_at_discretionary_action"]
        # 3 EXITs + every fresh review is "permitted": all fresh
        assert d["value"] == 1.0 and d["numerator"] == d["denominator"] == 40
        assert d["detail"]["recommended"] == {"n": 3, "fresh": 3}
        assert d["blocker"] is None
        # a stale review can never carry the discretionary recommendation
        # (the row builder holds it, and migration 206 CHECKs it)
        i += 1
        await _assess(conn, i, trigger=Q.T_MARKET, ev=Q.STALE, at=NOW - 30,
                      due_at=NOW - 31, rec="EXIT")
        d2 = _by_id(await Q.freshness_metrics(conn, NOW))[
            "freshness_at_discretionary_action"]
        assert d2["value"] == 1.0 and d2["denominator"] == 40
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_positions_monitored_reuses_management_view(monkeypatch):
    seen = {}

    async def view(conn, *, limit=100, now=None):
        seen["limit"], seen["now"] = limit, now
        return {"status": "OK", "positions": [
            {"position_kind": "PAPER", "state": "OPEN",
             "latest_review": {"at": 1}, "review_overdue": False},
            {"position_kind": "PAPER", "state": "OPEN",
             "latest_review": {"at": 1}, "review_overdue": True},
            {"position_kind": "ACTUAL", "state": "OPEN",
             "latest_review": None, "review_overdue": None},
            {"position_kind": "ACTUAL", "state": "OPEN",
             "latest_review": {"at": 1}, "review_overdue": False}],
            "summary": {"reviews_overdue": 1, "open_without_review": 1}}
    monkeypatch.setattr(XM, "management_view", view)
    conn = await asyncpg.connect(H.DSN)
    try:
        m = await Q.positions_monitored_metric(conn, NOW)
        assert seen == {"limit": Q.MONITORED_VIEW_LIMIT, "now": NOW}
        assert set(m) == KEYS
        assert (m["numerator"], m["denominator"], m["value"]) == (2, 4, 0.5)
        assert m["detail"]["reviews_overdue"] == 1
        assert m["detail"]["open_without_review"] == 1
        assert m["blocker"] == "1 open position(s) overdue, 1 never reviewed"

        async def empty(conn, *, limit=100, now=None):
            return {"status": "EMPTY", "positions": [], "summary": {}}
        monkeypatch.setattr(XM, "management_view", empty)
        e = await Q.positions_monitored_metric(conn, NOW)
        assert e["status"] == "UNAVAILABLE" and e["value"] is None
        assert e["why"] == "NO_OPEN_POSITION"
    finally:
        await conn.close()


@pg
async def test_positions_monitored_on_the_real_view():
    conn = await asyncpg.connect(H.DSN)
    try:
        m = await Q.positions_monitored_metric(conn, NOW)
        assert set(m) == KEYS
        assert m["status"] in ("MEASURED", "UNAVAILABLE",
                               "INSUFFICIENT_SAMPLE")
        if m["denominator"]:
            assert 0 <= m["value"] <= 1
        else:
            assert m["value"] is None
    finally:
        await conn.close()


@pg
async def test_the_scorecard_carries_the_new_set_and_renames_the_old():
    conn = await asyncpg.connect(H.DSN)
    try:
        card = await Q.scorecard(conn, now=NOW)
        agents = _by_id(card["domains"]["AGENTS"])
        for need in Q.FRESHNESS_IDS + ("positions_monitored_share",
                                       "reviews_fresh_share"):
            assert need in agents, need
            assert set(agents[need]) == KEYS
        old = agents["reviews_fresh_share"]
        assert old["name"] == Q.FRESH_SHARE_NAME
        assert "includes scheduled reviews of unchanged quotes" in \
            old["name"]
        assert old["blocker"] is None
        assert old["trend"]["direction"] in (None, "FLAT", "UP", "DOWN")
    finally:
        await conn.close()
