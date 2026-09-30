"""THE CALIBRATION MEASUREMENT RUNS ON A SCHEDULE, AND SAYS HOW FAR IT IS (D4).

`bettor_source_calibration.measure` had one caller, an admin route, so the
cohort could grow without ever being measured and a PASSED row would lapse
after 14 days. `cycle()` now runs it right after the outcome join, at most once
per CALIBRATION_MEASURE_EVERY_S, guarded against restarts by the newest
measured row and by the last run recorded in the loop's own heartbeat. The
heartbeat carries a compact digest, including the EXACT cohort shortfall.

What this does NOT change, and a test pins it: the entry gate's requirement
(current evaluator, >= 300 scored fixtures, <= 14 days, within tolerance), and
the evaluator's rule that only a completed PASSED/FAILED verdict is written.

Also here: the command centre's `entry_evidence` calls a row "measured" only
when the gate would (map5 §2).

SYNTHETIC EVIDENCE, LABELLED. Calibration rows inserted here carry
`measured_by` = D4_TEST_* and are removed in `finally`; the synthetic scored
rows in the shortfall test are built in memory and never written.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_source_calibration as CAL
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")


# ═════════════════════════════════════════════════════════════════════
# 1 · THE EXACT SHORTFALL (pure)
# ═════════════════════════════════════════════════════════════════════

def test_the_thresholds_come_from_the_evaluators_own_split():
    assert CAL.NEEDED_FOR_BASELINE == 150
    assert CAL.NEEDED_FOR_VERDICT == 449
    # the evaluator's reported figure is one more, and is labelled as its own
    assert CAL.cohort_shortfall(0)["evaluator_stated_total_required"] == 450


@pytest.mark.parametrize("n,shortfall,base_short", [
    (0, 449, 150), (53, 396, 97), (149, 300, 1), (150, 299, 0),
    (448, 1, 0), (449, 0, 0), (600, 0, 0), (None, 449, 150)])
def test_the_shortfall_is_a_count_of_fixtures(n, shortfall, base_short):
    got = CAL.cohort_shortfall(n)
    assert got["shortfall"] == shortfall
    assert got["shortfall_for_baseline"] == base_short
    assert got["needed_for_verdict"] == 449
    assert got["unit"] == "INDEPENDENT_RESOLVED_FIXTURES"


def _synthetic_rows(n):
    """n SYNTHETIC resolved fixtures, in scope, one per event_key."""
    out = []
    for i in range(n):
        out.append({"id": i + 1, "version": CAL.SOURCE_VERSION,
                    "devig_method": CAL.SOURCE_METHOD,
                    "sport_family": "baseball", "market": "h2h",
                    "event_key": "synthetic-%d" % i, "payout_event": "A",
                    "probability": 0.6 if i % 2 else 0.4,
                    "outcome_known": True, "outcome": 1 if i % 2 else 0,
                    "outcome_basis": "VENUE_SETTLEMENT_PRICE",
                    "observed_at_epoch": 1_700_000_000.0 + i})
    return out


def test_the_shortfall_agrees_with_when_a_verdict_becomes_possible():
    """448 fixtures: still INSUFFICIENT, shortfall 1. 449: a verdict."""
    short = CAL.evaluate(_synthetic_rows(448), measured_at=1.0)
    assert short["status"] == CAL.INSUFFICIENT
    assert short["cohort_shortfall"]["shortfall"] == 1
    full = CAL.evaluate(_synthetic_rows(449), measured_at=1.0)
    assert full["status"] in (CAL.PASSED, CAL.FAILED)
    assert full["cohort_shortfall"]["shortfall"] == 0
    assert full["cohort_shortfall"]["resolved_fixtures"] == \
        full["resolved_fixtures"] == 449
    # below the baseline minimum too
    tiny = CAL.evaluate(_synthetic_rows(53), measured_at=1.0)
    assert tiny["status"] == CAL.INSUFFICIENT
    assert tiny["cohort_shortfall"]["shortfall"] == 396
    assert CAL.evaluate([], measured_at=1.0)["cohort_shortfall"][
        "shortfall"] == 449


# ═════════════════════════════════════════════════════════════════════
# 2 · THE SCHEDULE AND ITS RESTART GUARD
# ═════════════════════════════════════════════════════════════════════

def test_the_schedule_constants_and_the_label():
    assert loop.CALIBRATION_MEASURE_EVERY_S == 6 * 3600.0
    assert loop.CALIBRATION_MEASURED_BY == "SCHEDULED_CALIBRATION_RUN"
    assert loop.CALIBRATION_MEASURE_WINDOW_DAYS == 90


def test_the_cycle_runs_it_right_after_the_join_and_reports_it():
    import inspect

    src = inspect.getsource(loop.cycle)
    join = src.index("joined = await join_outcomes(conn)")
    meas = src.index("await _scheduled_calibration_measurement(", join)
    assert join < meas < src.index("pair_observation = await", join)
    assert '"source_calibration_measurement": calibration_measurement' in src
    hb = inspect.getsource(loop._heartbeat)
    assert '"source_calibration_measurement":' in hb
    # the keys that were there stay there
    for kept in ('"odds_freshness": _freshness_digest(out)',
                 '"outcome_join": _outcome_join_digest(out.get("outcome_join"))',
                 '"refusals": out.get("refusals") or {}'):
        assert kept in hb, kept


def test_the_entry_gates_requirement_is_unchanged():
    assert loop.CALIBRATION_MAX_AGE_S == 14 * 86400.0
    assert CAL.MIN_RESOLVED_EVENTS == 300
    assert CAL.VERSION == "EXTERNAL_SOURCE_CALIBRATION_V2"
    # INSUFFICIENT is never a row
    row = CAL.to_row({"status": CAL.INSUFFICIENT}, measured_by="x",
                     window_start=0, window_end=1)
    assert row["write"] is False


def test_the_digest_carries_every_field_and_never_raises():
    m = {"ran": True, "attempted": True, "last_ran_at": 100.0,
         "next_due_at": 100.0 + loop.CALIBRATION_MEASURE_EVERY_S,
         "every_s": loop.CALIBRATION_MEASURE_EVERY_S,
         "measured_by": loop.CALIBRATION_MEASURED_BY, "window_days": 90,
         "evaluator": CAL.VERSION, "guard": {"read_errors": []},
         "result": dict(CAL.evaluate(_synthetic_rows(53), measured_at=100.0),
                        ran=True, would_write=False, written=False)}
    d = loop._calibration_measurement_digest(m)
    assert d["status"] == CAL.INSUFFICIENT
    assert d["resolved_fixtures"] == 53
    assert d["shortfall"]["shortfall"] == 396
    assert d["would_write"] is False and d["written"] is False
    assert d["next_due_at"] == m["next_due_at"]
    assert d["from_this_cycle"] is True
    assert d["scored_events"] is None        # nothing scored below 449
    # NOT DUE: the previous run's result is carried, and says so
    carried = loop._calibration_measurement_digest(
        {"ran": False, "why": "NOT_DUE", "last_ran_at": 100.0,
         "next_due_at": 200.0, "carried_last_run": d["last_run"]})
    assert carried["from_this_cycle"] is False
    assert carried["status"] == CAL.INSUFFICIENT
    assert carried["shortfall"]["shortfall"] == 396
    assert loop._calibration_measurement_digest(None) is None
    assert "digest_failed" in loop._calibration_measurement_digest(
        {"guard": "not a mapping"})


async def _connect():
    import asyncpg

    return await asyncpg.connect(DSN)


TEST_BY = ("D4_TEST_SCHEDULE_GUARD", "D4_TEST_DISPLAY")


async def _clean(conn):
    await conn.execute(
        "DELETE FROM external_source_calibration WHERE measured_by = ANY($1)",
        list(TEST_BY))


@pg
@pytest.mark.asyncio
async def test_it_runs_when_due_then_not_again_and_survives_a_restart(
        monkeypatch):
    conn = await _connect()
    saved_hb = await conn.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key=$1",
        loop.HEARTBEAT_KEY)
    saved_guard = loop._LAST_CALIBRATION_MEASURE[0]
    try:
        await _clean(conn)
        # A clean slate: no process run, no heartbeat record, and no recent
        # measured row for the source (another test's rows are older or
        # absent; the assertion says which).
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           loop.HEARTBEAT_KEY)
        loop._LAST_CALIBRATION_MEASURE[0] = 0.0
        newest = await conn.fetchval(loop.NEWEST_MEASUREMENT_SQL,
                                     devig.VERSION)
        now = time.time()
        assert newest is None or now - newest >= \
            loop.CALIBRATION_MEASURE_EVERY_S, "a recent row would block"

        calls = []
        real = CAL.measure

        async def recording(conn_, **kw):
            calls.append(kw)
            return await real(conn_, **kw)

        monkeypatch.setattr(CAL, "measure", recording)
        before = await conn.fetchval(
            "SELECT count(*) FROM external_source_calibration")

        # ── DUE: it runs, with the scheduled label, asking to write ─────
        got = await loop._scheduled_calibration_measurement(conn, now=now)
        assert got["ran"] is True and got["attempted"] is True
        assert calls and calls[0]["measured_by"] == "SCHEDULED_CALIBRATION_RUN"
        assert calls[0]["write"] is True and calls[0]["days"] == 90
        assert got["next_due_at"] == pytest.approx(
            now + loop.CALIBRATION_MEASURE_EVERY_S)
        # the evaluator wrote only if it reached a verdict; this database
        # holds far fewer than 449 resolved fixtures
        res = got["result"]
        assert res["status"] in (CAL.INSUFFICIENT, CAL.OUT_OF_SCOPE)
        assert res["written"] is False
        assert await conn.fetchval(
            "SELECT count(*) FROM external_source_calibration") == before

        # ── NOT DUE: the same process does not run it again ───────────
        again = await loop._scheduled_calibration_measurement(
            conn, now=now + 60.0)
        assert again["ran"] is False and again["why"] == "NOT_DUE"
        assert len(calls) == 1

        # ── A RESTART: the process guard is gone; the heartbeat is not ──
        digest = loop._calibration_measurement_digest(got)
        await loop._heartbeat(conn, {"state": "LIVE",
                                     "source_calibration_measurement": got})
        loop._LAST_CALIBRATION_MEASURE[0] = 0.0
        restarted = await loop._scheduled_calibration_measurement(
            conn, now=now + 120.0)
        assert restarted["ran"] is False and restarted["why"] == "NOT_DUE"
        assert restarted["guard"]["heartbeat_last_ran_at"] == \
            pytest.approx(now)
        assert restarted["carried_last_run"]["status"] == res["status"]
        assert len(calls) == 1
        carried = loop._calibration_measurement_digest(restarted)
        assert carried["status"] == digest["status"]
        assert carried["from_this_cycle"] is False

        # ── A RESTART WITH NO HEARTBEAT, BUT A RECENT MEASURED ROW ──────
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           loop.HEARTBEAT_KEY)
        loop._LAST_CALIBRATION_MEASURE[0] = 0.0
        await conn.execute(
            "INSERT INTO external_source_calibration (source_version, "
            "measured_at, window_start, window_end, sample_size, metric, "
            "score, tolerance, within_tolerance, measured_by, provenance) "
            "VALUES ($1, now() - interval '1 hour', now() - interval "
            "'91 days', now() - interval '1 hour', 1, 'BRIER', 0.2, 0.24, "
            "FALSE, 'D4_TEST_SCHEDULE_GUARD', '{\"note\": \"synthetic\"}')",
            devig.VERSION)
        guarded = await loop._scheduled_calibration_measurement(
            conn, now=time.time())
        assert guarded["ran"] is False and guarded["why"] == "NOT_DUE"
        assert guarded["guard"]["newest_measured_row_at"] is not None
        assert len(calls) == 1

        # ── AND ONCE THE INTERVAL HAS PASSED, IT RUNS AGAIN ─────────────
        late = await loop._scheduled_calibration_measurement(
            conn, now=time.time() + loop.CALIBRATION_MEASURE_EVERY_S + 60.0)
        assert late["ran"] is True and len(calls) == 2
    finally:
        await _clean(conn)
        loop._LAST_CALIBRATION_MEASURE[0] = saved_guard
        if saved_hb is None:
            await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                               loop.HEARTBEAT_KEY)
        else:
            await conn.execute(
                "INSERT INTO ingestion_state (key, value) VALUES ($1, "
                "$2::jsonb) ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
                loop.HEARTBEAT_KEY, saved_hb)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_heartbeat_persists_the_digest():
    conn = await _connect()
    saved_hb = await conn.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key=$1",
        loop.HEARTBEAT_KEY)
    try:
        m = {"ran": True, "attempted": True, "last_ran_at": 100.0,
             "next_due_at": 100.0 + loop.CALIBRATION_MEASURE_EVERY_S,
             "result": dict(CAL.evaluate(_synthetic_rows(53),
                                         measured_at=100.0),
                            ran=True, would_write=False, written=False)}
        await loop._heartbeat(conn, {"state": "LIVE",
                                     "source_calibration_measurement": m,
                                     "refusals": {"X": 1}})
        hb = json.loads(await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1",
            loop.HEARTBEAT_KEY))
        d = hb["source_calibration_measurement"]
        for k in ("status", "resolved_fixtures", "scored_events",
                  "shortfall", "would_write", "written", "next_due_at",
                  "last_ran_at"):
            assert k in d, k
        assert d["shortfall"]["shortfall"] == 396
        assert hb["refusals"] == {"X": 1}
    finally:
        if saved_hb is None:
            await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                               loop.HEARTBEAT_KEY)
        else:
            await conn.execute(
                "INSERT INTO ingestion_state (key, value) VALUES ($1, "
                "$2::jsonb) ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
                loop.HEARTBEAT_KEY, saved_hb)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · THE DISPLAY CALLS A ROW "MEASURED" ONLY WHEN THE GATE WOULD
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("label,evaluator,sample,age_days,measured,code", [
    ("old_evaluator", "EXTERNAL_SOURCE_CALIBRATION_V1", 412, 1, False,
     "CALIBRATION_ROW_NOT_FROM_THE_CURRENT_EVALUATOR"),
    ("below_minimum", CAL.VERSION, 120, 1, False,
     "CALIBRATION_ROW_BELOW_THE_EVALUATORS_MINIMUM"),
    ("stale", CAL.VERSION, 412, 20, False, "CALIBRATION_ROW_IS_STALE"),
    ("current", CAL.VERSION, 412, 1, True, None),
])
async def test_entry_evidence_agrees_with_the_gate(label, evaluator, sample,
                                                   age_days, measured, code):
    from sportsassets.api import command_rn1x as RN

    conn = await _connect()
    try:
        await _clean(conn)
        existing = await conn.fetchval(
            "SELECT count(*) FROM external_source_calibration WHERE "
            "source_version=$1 AND measured_at > now() - "
            "($2 || ' days')::interval", devig.VERSION, str(age_days + 1))
        if existing:
            pytest.skip("another row for the source is newer than the one "
                        "this case inserts; the newest row is the subject")
        await conn.execute(
            "INSERT INTO external_source_calibration (source_version, "
            "measured_at, window_start, window_end, sample_size, metric, "
            "score, tolerance, within_tolerance, measured_by, provenance) "
            "VALUES ($1, now() - ($2 || ' days')::interval, now() - interval "
            "'100 days', now(), $3, 'BRIER', 0.21, 0.24, TRUE, "
            "'D4_TEST_DISPLAY', $4::jsonb)",
            devig.VERSION, str(age_days), sample,
            json.dumps({"evaluator": evaluator, "note": "SYNTHETIC: %s"
                        % label}))
        ev = await RN.entry_evidence(conn, hours=1, limit=5)
        cal = ev["source_calibration"]
        gate = await loop.source_calibration(conn, devig.VERSION)
        assert cal["measured"] is measured, cal
        assert gate["measured"] is measured
        assert cal.get("error") == gate.get("error") == code
        assert "verdict_source" in cal
    finally:
        await _clean(conn)
        await conn.close()
