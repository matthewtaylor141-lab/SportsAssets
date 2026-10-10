"""CAPITAL-CRITICAL: THE COVERAGE STEP IS BOUNDED AND BACKS OFF; ITS STALENESS
IS NAMED, NEVER SHOWN AS CURRENT (RC6.3b pass-stall).

Production, 2026-10-10: ingestion_state['coverage_integrity_last'] last
advanced 02:03:59Z, the newest coverage_funnel_snapshots row was 03:50:51Z,
and every paper pass died inside the coverage step's first league statement
(UTC yesterday, EVALUATED_SQL, 50+ s on the pooled connection). The step wrote
its watermark only AFTER run() completed, so a run that never completed left
the step due on the next pass -- every pass died the same way.

Proved here, on a real Postgres:

  * coverage_integrity.step runs run() under its own budget (RUN_BUDGET_S,
    cut to the pass time left); on timeout or error it STILL advances its
    watermark with the failure recorded ({"at", "version", "timed_out",
    "why"}), so the next pass is NOT_DUE and the step is due again only after
    REFRESH_EVERY_S; it returns a named error code and names the statement
    that was in flight;
  * a run that is cut keeps the days it persisted (a day is written whole or
    not at all); the cancelled server-side query is not left running and the
    connection is usable;
  * a single league statement is bounded by a statement timeout of its own: a
    source that cannot be read in time is NULL with SOURCE_READ_FAILED, never a
    zero, and the rest of the run goes on;
  * a failed or timed-out run is written to Audrey as one finding per day;
  * coverage_payload (Command) reports snapshots older than the refresh
    interval as stale BY NAME (COVERAGE_SNAPSHOTS_STALE) with their age and
    the last attempt's failure, row by row, and never as current; a final past
    day is not stale by design.

SYNTHETIC rows on far-past days in a scratch test database; no venue.
"""
from __future__ import annotations

import asyncio
import json
import time

import asyncpg
import pytest

from sportsassets.agents import coverage_integrity as C

from tests import paper_harness as H
from tests.rc63b_cov_support import (  # noqa: F401  (main_account is a fixture)
    DAY, GUARD_S, NOW, TAG, clean, league_name, main_account, run_step,
    seed_league, step_ctx, watermark)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

_league = league_name
_seed = seed_league
_clean = clean
_ctx = step_ctx
_watermark = watermark
_step = run_step


def _hang_from(monkeypatch, nth: int):
    """funnel_for_day answers normally `nth` times and then hangs."""
    real = C.funnel_for_day
    calls = {"n": 0}

    async def wrapped(conn, day, tz, *a, **kw):
        calls["n"] += 1
        if calls["n"] > nth:
            await asyncio.sleep(3600)
        return await real(conn, day, tz, *a, **kw)
    monkeypatch.setattr(C, "funnel_for_day", wrapped)
    return calls


# ═════════════════════════════════════════════════════════════════════
# THE PINS
# ═════════════════════════════════════════════════════════════════════

def test_the_budget_is_well_under_the_pass_bound():
    from sportsassets.agents import paper_runtime as PRT
    bound = PRT.HARD_TIMEOUT_S - PRT.PASS_RECORD_RESERVE_S
    assert C.RUN_BUDGET_S <= bound / 2
    assert C.STEP_WRITE_MARGIN_S + C.MIN_RUN_BUDGET_S < C.RUN_BUDGET_S
    assert C.REFRESH_EVERY_S == 900.0
    assert C.R_RUN_TIMED_OUT == "COVERAGE_RUN_EXCEEDED_ITS_BUDGET"
    assert C.R_RUN_FAILED == "COVERAGE_RUN_FAILED"
    assert C.R_SNAPSHOTS_STALE == "COVERAGE_SNAPSHOTS_STALE"
    assert C.R_NO_PASS_TIME == "COVERAGE_STEP_HAD_NO_PASS_TIME"


# ═════════════════════════════════════════════════════════════════════
# THE WATERMARK ADVANCES ON A TIMEOUT (THE REGRESSION)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_run_over_its_budget_advances_the_watermark_and_is_not_due_next_pass(
        main_account, monkeypatch):
    """FAILS on af40bea2: run() has no budget, the step waits on it (here
    until the guard) and writes no watermark, so the next pass runs it again."""
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        league = _league()
        await _seed(conn, league)
        monkeypatch.setattr(C, "RUN_BUDGET_S", 1.0, raising=False)
        # UTC yesterday and today are computed and persisted; the third
        # window (New York yesterday) hangs
        _hang_from(monkeypatch, 2)

        t0 = time.monotonic()
        res = await _step(conn, _ctx(a, NOW))
        took = time.monotonic() - t0
        assert took < 5.0, "the step ended at its own budget"
        assert res["ran"] is False
        assert res["timed_out"] is True
        assert res["error"] == C.R_RUN_TIMED_OUT
        assert res["snapshots"] >= 2
        assert res["in_flight"], "the window that was in flight is named"

        # THE WATERMARK ADVANCED, with the failure recorded
        wm = await _watermark(conn)
        assert wm is not None and wm["at"] == NOW
        assert wm["version"] == C.VERSION
        assert wm["timed_out"] is True
        assert wm["why"].startswith(C.R_RUN_TIMED_OUT)
        assert wm["snapshots"] == res["snapshots"]
        assert wm["in_flight"] == res["in_flight"]

        # A PARTIAL RUN KEEPS THE DAYS IT PERSISTED
        kept = await conn.fetch(
            "SELECT tz, day FROM coverage_funnel_snapshots WHERE league=$1 "
            " ORDER BY tz, day", league)
        assert [(r["tz"], r["day"].isoformat()) for r in kept] == [
            ("UTC", "2026-03-13"), ("UTC", "2026-03-14")]

        # THE NEXT PASS IS NOT DUE (and does not call run at all)
        async def never(*a_, **k_):
            raise AssertionError("run() was called while backed off")
        monkeypatch.setattr(C, "run", never)
        again = await _step(conn, _ctx(a, NOW + 120.0))
        assert again["ran"] is False and again["why"] == "NOT_DUE"
        assert again["last_at"] == NOW
        assert again["backed_off"] == C.R_RUN_TIMED_OUT
        assert 0 < again["next_due_in_s"] <= C.REFRESH_EVERY_S - 120.0 + 1
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_the_step_is_due_again_only_after_the_refresh_interval(
        main_account, monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        league = _league()
        await _seed(conn, league)
        monkeypatch.setattr(C, "RUN_BUDGET_S", 0.8, raising=False)
        real = C.funnel_for_day
        mode = {"hang": True}

        async def switchable(conn_, day, tz, *a_, **k_):
            if mode["hang"]:
                await asyncio.sleep(3600)
            return await real(conn_, day, tz, *a_, **k_)
        monkeypatch.setattr(C, "funnel_for_day", switchable)

        first = await _step(conn, _ctx(a, NOW))
        assert first["timed_out"] is True
        mode["hang"] = False
        just_before = await _step(conn, _ctx(a, NOW + C.REFRESH_EVERY_S - 1))
        assert just_before["why"] == "NOT_DUE"
        due = await _step(conn, _ctx(a, NOW + C.REFRESH_EVERY_S))
        assert due["ran"] is True and due.get("error") is None, due
        assert due["snapshots"] >= 4
        wm = await _watermark(conn)
        assert wm["at"] == NOW + C.REFRESH_EVERY_S
        assert not wm.get("timed_out"), "a good run clears the failure"
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_an_error_also_advances_the_watermark_and_is_named(
        main_account, monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)

        async def boom(*a_, **k_):
            raise RuntimeError("synthetic coverage failure")
        monkeypatch.setattr(C, "run", boom)
        res = await _step(conn, _ctx(a, NOW))
        assert res["ran"] is False and res["error"] == C.R_RUN_FAILED
        assert "synthetic coverage failure" in res["why"]
        wm = await _watermark(conn)
        assert wm["at"] == NOW and wm["timed_out"] is False
        assert wm["failed"] is True
        assert wm["why"].startswith(C.R_RUN_FAILED)
        again = await _step(conn, _ctx(a, NOW + 60.0))
        assert again["why"] == "NOT_DUE" and again["backed_off"] == \
            C.R_RUN_FAILED
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_a_good_run_is_recorded_as_before(main_account):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        league = _league()
        await _seed(conn, league)
        res = await _step(conn, _ctx(a, NOW))
        assert res["ran"] is True and res.get("error") is None
        assert res["snapshots"] >= 4 and "errors" in res
        wm = await _watermark(conn)
        assert set(wm) == {"at", "version", "alerts"}      # unchanged shape
        assert wm["at"] == NOW
        rows = await conn.fetchval(
            "SELECT count(*) FROM coverage_funnel_snapshots WHERE league=$1",
            league)
        assert rows == 4
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_not_the_main_account_is_unchanged(main_account):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        res = await _step(conn, _ctx(dict(a, account_id="paper_other"), NOW))
        assert res == {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
        assert await _watermark(conn) is None
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE BUDGET NEVER EXCEEDS THE PASS TIME LEFT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_budget_is_cut_to_the_pass_time_left_and_leaves_time_to_write(
        main_account, monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        monkeypatch.setattr(C, "RUN_BUDGET_S", 60.0, raising=False)
        _hang_from(monkeypatch, 0)
        # the pass has 4.5 s left for this step: 4.5 - 3 s kept to write = 1.5
        t0 = time.monotonic()
        res = await _step(conn, _ctx(a, NOW, step_deadline=time.monotonic()
                                     + 4.5))
        took = time.monotonic() - t0
        assert res["timed_out"] is True
        assert 1.0 <= took < 3.5, took
        assert res["budget_s"] <= 1.6
        assert (await _watermark(conn))["timed_out"] is True
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_with_no_pass_time_to_run_the_watermark_is_left_alone(
        main_account, monkeypatch):
    """The pass gave this step too little time to do anything: nothing failed
    in coverage, so nothing is backed off -- the step is simply due again."""
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        monkeypatch.setattr(C, "MIN_RUN_BUDGET_S", 2.0, raising=False)      # production's

        async def never(*a_, **k_):
            raise AssertionError("run() must not start without time")
        monkeypatch.setattr(C, "run", never)
        res = await _step(conn, _ctx(a, NOW, step_deadline=time.monotonic()
                                     + C.STEP_WRITE_MARGIN_S + 0.5))
        assert res["ran"] is False and res["why"] == C.R_NO_PASS_TIME
        assert await _watermark(conn) is None
    finally:
        await _clean(conn)
        await conn.close()


class _CutProxy:
    """The step's connection, delegating to the real one, hanging on the
    `hang_at`-th INSERT into coverage_funnel_snapshots (a hang BETWEEN two
    league rows of one day)."""

    def __init__(self, real, hang_at: int):
        self._real = real
        self._hang_at = hang_at
        self.inserts = 0

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def execute(self, sql, *args, **kw):
        if "INSERT INTO coverage_funnel_snapshots" in sql:
            self.inserts += 1
            if self.inserts == self._hang_at:
                await asyncio.sleep(3600)
        return await self._real.execute(sql, *args, **kw)


@pg
async def test_a_cut_day_is_written_whole_or_not_at_all(main_account,
                                                        monkeypatch):
    """persist_day is one transaction: cancelled between two league rows of a
    day it leaves none of that day's rows, and the days before it stay."""
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        la, lb = _league(), _league()
        await _seed(conn, la)
        await _seed(conn, lb)
        monkeypatch.setattr(C, "RUN_BUDGET_S", 1.5, raising=False)
        proxy = _CutProxy(conn, hang_at=6)     # 2 rows a day: the 2nd of day 3
        res = await asyncio.wait_for(C.step(proxy, _ctx(a, NOW)), GUARD_S)
        assert res["timed_out"] is True and res["snapshots"] == 4
        days = await conn.fetch(
            "SELECT tz, day, count(*) AS n FROM coverage_funnel_snapshots "
            " WHERE league = ANY($1::text[]) GROUP BY 1,2 ORDER BY 1,2",
            [la, lb])
        # two whole days (both leagues each), nothing of the cut third
        assert [(r["tz"], r["day"].isoformat(), r["n"]) for r in days] == [
            ("UTC", "2026-03-13", 2), ("UTC", "2026-03-14", 2)]
        assert proxy.inserts == 6
        assert not conn.is_in_transaction()
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# AUDREY SEES IT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_failed_run_is_one_audrey_finding_per_day(main_account,
                                                          monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        monkeypatch.setattr(C, "RUN_BUDGET_S", 0.6, raising=False)
        _hang_from(monkeypatch, 0)
        await _step(conn, _ctx(a, NOW))
        await _step(conn, _ctx(a, NOW + C.REFRESH_EVERY_S + 1))
        rows = await conn.fetch(
            "SELECT * FROM paper_audrey_findings WHERE session_id=$1 AND "
            " kind=$2", a["session_id"], C.R_RUN_FAILED)
        assert len(rows) == 1, "one finding for the day, not one per attempt"
        f = rows[0]
        assert f["severity"] == "WARNING"
        d = json.loads(f["detail"])
        assert d["code"] == C.R_RUN_TIMED_OUT and d["timed_out"] is True
        assert d["refresh_every_s"] == C.REFRESH_EVERY_S
        assert d["stale_after_s"] == C.REFRESH_EVERY_S + C.STALE_GRACE_S
        assert d["in_flight"]
        # the next UTC day is a new finding
        await _step(conn, _ctx(a, NOW + DAY + 2 * C.REFRESH_EVERY_S))
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_audrey_findings WHERE session_id=$1 "
            " AND kind=$2", a["session_id"], C.R_RUN_FAILED) == 2
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# COMMAND: STALE SNAPSHOTS ARE STALE BY NAME
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_snapshots_older_than_the_refresh_interval_are_reported_stale_by_name(
        main_account):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        league = _league()
        await _seed(conn, league)
        # persisted 2000 s ago (> REFRESH_EVERY_S + STALE_GRACE_S = 1020 s)
        res = await C.run(conn, now=NOW - 2000.0, ctx=None, days=2)
        assert res["ran"]
        body = await C.coverage_payload(conn, tz="UTC", days=7, now=NOW)
        fr = body["snapshot_freshness"]
        assert fr["status"] == C.R_SNAPSHOTS_STALE
        assert fr["code"] == C.R_SNAPSHOTS_STALE
        assert fr["age_s"] == pytest.approx(2000.0, abs=1.0)
        assert fr["refresh_every_s"] == C.REFRESH_EVERY_S
        assert fr["stale_after_s"] == C.REFRESH_EVERY_S + C.STALE_GRACE_S
        today = next(d for d in body["days"] if d["day"] == "2026-03-14")
        row = next(r for r in today["leagues"] if r["league"] == league)
        assert row["stale"] is True and row["age_s"] == \
            pytest.approx(2000.0, abs=1.0)
        # YESTERDAY, finalised by that run (it ran after midnight? no: at
        # NOW - 2000 s it is still 2026-03-14 UTC, so yesterday was final)
        yest = next(d for d in body["days"] if d["day"] == "2026-03-13")
        yrow = next(r for r in yest["leagues"] if r["league"] == league)
        assert yrow["final"] is True and yrow["stale"] is False
        # the last attempt's failure is part of the same answer
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            C.WATERMARK_KEY, json.dumps({
                "at": NOW - 60.0, "version": C.VERSION, "timed_out": True,
                "why": "%s: test" % C.R_RUN_TIMED_OUT}))
        body = await C.coverage_payload(conn, tz="UTC", days=7, now=NOW)
        la = body["snapshot_freshness"]["last_attempt"]
        assert la["timed_out"] is True and la["at"] == NOW - 60.0
        assert la["why"].startswith(C.R_RUN_TIMED_OUT)
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_fresh_snapshots_are_current_and_a_pass_that_is_merely_late_is_not_stale(
        main_account):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        league = _league()
        await _seed(conn, league)
        # computed 1000 s ago: past REFRESH_EVERY_S (900) but inside the
        # grace a late pass is allowed -- NOT stale
        res = await C.run(conn, now=NOW - 1000.0, ctx=None, days=2)
        assert res["ran"]
        body = await C.coverage_payload(conn, tz="UTC", days=7, now=NOW)
        fr = body["snapshot_freshness"]
        assert fr["status"] == "CURRENT" and fr["code"] is None
        assert fr["age_s"] == pytest.approx(1000.0, abs=1.0)
        today = next(d for d in body["days"] if d["day"] == "2026-03-14")
        assert all(r["stale"] is False for r in today["leagues"]
                   if r["league"] == league)
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_with_no_snapshot_in_the_window_the_freshness_says_so_and_never_current():
    conn = await asyncpg.connect(H.DSN)
    try:
        fr = await C.snapshot_freshness(conn, now=NOW + 400 * DAY, tz="UTC")
        assert fr["status"] == "NO_SNAPSHOTS" and fr["code"] is None
        assert fr["newest_computed_at"] is None and fr["age_s"] is None
    finally:
        await _clean(conn)
        await conn.close()
