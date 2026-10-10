"""CAPITAL-CRITICAL: A FAILED COVERAGE READ NEITHER FINALISES A DAY NOR ERASES
A MEASURED VALUE (RC6.3c pass-hardening, N1).

Found by an independent review of the RC6.3b pass-stall fix. persist_day
upserted every column with EXCLUDED and set `final = now >= window_end`,
guarded by `WHERE NOT final`. So when the FIRST run after a day ended had a
read cut by READ_STATEMENT_TIMEOUT_MS (or any SOURCE_READ_FAILED), the day was
finalised with that column NULL -- over the good non-final numbers an earlier
run had read -- and a final row is never written again: no later healthy run
could repair it.

The reviewer's reproduction, on a real Postgres: a run at 23:00Z on day D
(evaluated_events = 3, not final), then one at 00:05Z with EVALUATED_SQL cut by
the statement timeout -- the D row was final = True with evaluated_events NULL,
and the healthy run after it left it so.

Proved here (the tests marked FAILS ON THE BASE fail on 5979416f / f971d665):

  * a day with a failed read is never marked final, however long ago it ended;
  * a failed read never overwrites a previously read non-NULL value of a row
    that is not final: the last good value is kept, the failure is recorded BY
    NAME in `unavailable` (SOURCE_READ_FAILED:<error>, then LAST_GOOD_VALUE_KEPT)
    and the ratios are recomputed from the values written;
  * a column with no earlier value stays NULL (never a zero) with its reason;
  * a later healthy run reads the day again and finalises it, with the fresh
    values and an empty `unavailable`;
  * a final row is still never rewritten -- not by a healthy run, not by a
    failed one;
  * only a FAILED read keeps a day open: an absent table and a stage the
    ledger does not record are what the source is on every run, so they do
    not (the pre-existing finalisation is unchanged for them);
  * the columns whose reads succeeded are always written fresh, never kept.

Final rows written BEFORE this change with a failed read are not rewritten
(the guard `WHERE NOT final` is unchanged); that is a known limit, not a claim.

SYNTHETIC rows on far-past days in a scratch test database; no venue.
"""
from __future__ import annotations

import datetime as _dt
import json

import pytest

from sportsassets.agents import coverage_integrity as C

from tests import paper_harness as H
from tests.rc63b_cov_support import clean, league_name, seed_league

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

UTC = _dt.timezone.utc
#: the day under test is 2026-03-13 (UTC); NOW is 2026-03-14 18:00Z
D = _dt.date(2026, 3, 13)
#: 23:00Z on D: D is still "today"
BEFORE_MIDNIGHT = _dt.datetime(2026, 3, 13, 23, 0, tzinfo=UTC).timestamp()
#: the first run after D ended, and two more
AFTER_MIDNIGHT = _dt.datetime(2026, 3, 14, 0, 5, tzinfo=UTC).timestamp()
LATER = AFTER_MIDNIGHT + 900.0
LATEST = AFTER_MIDNIGHT + 1800.0

#: a league statement that hangs; the read's own statement timeout cuts it
WEDGED_EVALUATED_SQL = (
    "SELECT pg_sleep(3600), 'x'::text AS league, 1 AS n "
    "FROM (SELECT $1::float8 AS a, $2::float8 AS b) p")

ALL_COLUMNS = [C.COLUMN[s] for s in C.STAGES] + list(C.EXTRA_COLUMNS)


def _wedge_evaluated(monkeypatch):
    monkeypatch.setattr(C, "EVALUATED_SQL", WEDGED_EVALUATED_SQL)
    monkeypatch.setattr(C, "READ_STATEMENT_TIMEOUT_MS", 300)


async def _row(conn, league, *, tz="UTC", day=D):
    r = await conn.fetchrow(
        "SELECT final, evaluated_events, unavailable, ratios "
        "  FROM coverage_funnel_snapshots "
        " WHERE tz=$1 AND day=$2 AND league=$3", tz, day, league)
    if r is None:
        return None
    d = dict(r)
    for k in ("unavailable", "ratios"):
        if isinstance(d[k], str):
            d[k] = json.loads(d[k])
    return d


async def _all_rows(conn, league, *, tz="UTC") -> dict:
    out = {}
    for r in await conn.fetch(
            "SELECT day, final, evaluated_events, unavailable "
            "  FROM coverage_funnel_snapshots WHERE tz=$1 AND league=$2",
            tz, league):
        un = r["unavailable"]
        out[r["day"]] = (r["final"], r["evaluated_events"],
                         json.loads(un) if isinstance(un, str) else un)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE REVIEWER'S REPRODUCTION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_read_cut_after_midnight_neither_finalises_the_day_nor_erases_the_value(
        monkeypatch):
    """FAILS ON THE BASE: the D row is final=True with evaluated_events NULL."""
    conn = await H.connect()
    try:
        await clean(conn)
        league = league_name()
        await seed_league(conn, league)

        await C.run(conn, now=BEFORE_MIDNIGHT)
        before = await _row(conn, league)
        assert before["final"] is False and before["evaluated_events"] == 3
        assert before["unavailable"] == {}

        # the first run after D ended: the EVALUATED read is cut by its timeout
        _wedge_evaluated(monkeypatch)
        await C.run(conn, now=AFTER_MIDNIGHT)
        cut = await _row(conn, league)
        assert cut["final"] is False, "a day with a failed read is not final"
        assert cut["evaluated_events"] == 3, "the last good value is kept"
        why = cut["unavailable"]["evaluated_events"]
        assert why.startswith(C.R_READ_FAILED), why
        assert why.endswith(C.KEPT_LAST_GOOD), why
        # the ratios are from the values written: the kept value is a number
        r = cut["ratios"]["settlement_supported->evaluated"]
        assert r["ratio"] == 1.0 and r["why"] is None, r
        assert cut["ratios"]["evaluated->decided"]["why"] != C.R_DENOM_NULL
        # only the failed column is named
        assert list(cut["unavailable"]) == ["evaluated_events"]
        # (today's row has no earlier value: NULL with the reason, never 0)
        today = await _row(conn, league, day=D + _dt.timedelta(days=1))
        assert today["final"] is False and today["evaluated_events"] is None
        assert today["unavailable"]["evaluated_events"].startswith(
            C.R_READ_FAILED)
        assert not today["unavailable"]["evaluated_events"].endswith(
            C.KEPT_LAST_GOOD)

        # a later HEALTHY run reads D again and finalises it
        monkeypatch.undo()
        await C.run(conn, now=LATER)
        healed = await _row(conn, league)
        assert healed["final"] is True and healed["evaluated_events"] == 3
        assert healed["unavailable"] == {}
        assert (await _row(conn, league, day=D + _dt.timedelta(days=1))
                )["evaluated_events"] == 3

        # ... and a final row is never written again, even by a failed read
        _wedge_evaluated(monkeypatch)
        await C.run(conn, now=LATEST)
        still = await _row(conn, league)
        assert still["final"] is True and still["evaluated_events"] == 3
        assert still["unavailable"] == {}
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_the_first_ever_run_after_midnight_with_a_cut_read_leaves_null_and_the_day_open(
        monkeypatch):
    """No earlier value to keep: the column stays NULL (never a zero), named,
    and the day stays open until a healthy run finalises it.
    FAILS ON THE BASE: the day is final=True at once."""
    conn = await H.connect()
    try:
        await clean(conn)
        league = league_name()
        await seed_league(conn, league)
        _wedge_evaluated(monkeypatch)
        await C.run(conn, now=AFTER_MIDNIGHT)
        cut = await _row(conn, league)
        assert cut["final"] is False
        assert cut["evaluated_events"] is None
        assert cut["unavailable"]["evaluated_events"].startswith(
            C.R_READ_FAILED)
        # the columns whose reads SUCCEEDED were written fresh
        assert cut["ratios"]["provider->normalized"]["ratio"] == 1.0

        monkeypatch.undo()
        await C.run(conn, now=LATER)
        healed = await _row(conn, league)
        assert healed["final"] is True and healed["evaluated_events"] == 3
        assert healed["unavailable"] == {}
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_consecutive_failed_reads_keep_the_first_good_value_and_never_finalise(
        monkeypatch):
    conn = await H.connect()
    try:
        await clean(conn)
        league = league_name()
        await seed_league(conn, league)
        await C.run(conn, now=BEFORE_MIDNIGHT)
        _wedge_evaluated(monkeypatch)
        for at in (AFTER_MIDNIGHT, LATER, LATEST):
            await C.run(conn, now=at)
            row = await _row(conn, league)
            assert row["final"] is False, at
            assert row["evaluated_events"] == 3, at
            assert row["unavailable"]["evaluated_events"].endswith(
                C.KEPT_LAST_GOOD)
        monkeypatch.undo()
        await C.run(conn, now=LATEST + 900.0)
        assert (await _row(conn, league))["final"] is True
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_a_new_york_day_is_held_open_by_a_failed_read_the_same_way(
        monkeypatch):
    """The America/New_York day of 2026-03-13 ends at 04:00Z on the 14th
    (EDT): the first run after it, with a cut read, keeps the value."""
    ny_after = _dt.datetime(2026, 3, 14, 4, 5, tzinfo=UTC).timestamp()
    ny_before = _dt.datetime(2026, 3, 14, 3, 0, tzinfo=UTC).timestamp()
    conn = await H.connect()
    try:
        await clean(conn)
        league = league_name()
        await seed_league(conn, league)
        await C.run(conn, now=ny_before)
        good = await _row(conn, league, tz="America/New_York")
        assert good["final"] is False and good["evaluated_events"] == 3
        _wedge_evaluated(monkeypatch)
        await C.run(conn, now=ny_after)
        cut = await _row(conn, league, tz="America/New_York")
        assert cut["final"] is False and cut["evaluated_events"] == 3
        monkeypatch.undo()
        await C.run(conn, now=ny_after + 900.0)
        healed = await _row(conn, league, tz="America/New_York")
        assert healed["final"] is True and healed["evaluated_events"] == 3
    finally:
        await clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# persist_day ITSELF, COLUMN BY COLUMN
# ═════════════════════════════════════════════════════════════════════

def _funnel(league, *, unavailable=None, tz="UTC", day=D, **cols) -> dict:
    """A computed day as funnel_for_day returns it: `cols` are the measured
    counts (every other column 0), `unavailable` {column: reason} are NULL."""
    start, end = C.day_window(day, tz)
    row = {"league": league, "sport_family": "family"}
    for c in ALL_COLUMNS:
        row[c] = cols.get(c, 0)
    un = dict(unavailable or {})
    for c in un:
        row[c] = None
    row["unavailable"] = un
    row["ratios"] = C.ratios(row)
    return {"day": day.isoformat(), "tz": tz, "window": [start, end],
            "leagues": {league: row}, "unavailable": dict(un), "sources": {}}


async def _snap(conn, league, *, tz="UTC", day=D) -> dict:
    r = await conn.fetchrow(
        "SELECT * FROM coverage_funnel_snapshots WHERE tz=$1 AND day=$2 AND "
        "league=$3", tz, day, league)
    d = dict(r)
    for k in ("unavailable", "ratios"):
        if isinstance(d[k], str):
            d[k] = json.loads(d[k])
    return d


FAILED = C.R_READ_FAILED + ":QueryCanceledError"


@pg
async def test_each_failed_column_keeps_its_own_last_good_value_and_the_rest_are_fresh():
    conn = await H.connect()
    try:
        await clean(conn)
        league = league_name()
        # the earlier healthy run: the whole day, not final
        await C.persist_day(conn, _funnel(
            league, provider_events=10, normalized_events=9,
            venue_discovered=8, mapped_events=7, settlement_supported=6,
            evaluated_events=5, decided_events=4, entered_events=2,
            refused_events=2, ordered_events=2, filled_events=1,
            actual_intents=2, actual_submitted=2, actual_filled=1,
            venue_catalogue_events=3), now=BEFORE_MIDNIGHT)
        # the next run: the ACTUAL source and the decisions source fail; the
        # provider, evaluated and the other reads succeeded with NEW counts
        await C.persist_day(conn, _funnel(
            league, unavailable={"decided_events": FAILED,
                                 "entered_events": FAILED,
                                 "refused_events": FAILED,
                                 "actual_intents": FAILED,
                                 "actual_submitted": FAILED,
                                 "actual_filled": FAILED},
            provider_events=12, normalized_events=11, venue_discovered=9,
            mapped_events=8, settlement_supported=7, evaluated_events=6,
            ordered_events=3, filled_events=2, venue_catalogue_events=4),
            now=AFTER_MIDNIGHT)
        s = await _snap(conn, league)
        assert s["final"] is False
        # fresh where the read succeeded
        assert (s["provider_events"], s["evaluated_events"],
                s["ordered_events"], s["filled_events"],
                s["venue_catalogue_events"]) == (12, 6, 3, 2, 4)
        # kept where it failed: each column its OWN earlier value
        assert (s["decided_events"], s["entered_events"],
                s["refused_events"]) == (4, 2, 2)
        assert (s["actual_intents"], s["actual_submitted"],
                s["actual_filled"]) == (2, 2, 1)
        assert sorted(s["unavailable"]) == sorted(
            ["decided_events", "entered_events", "refused_events",
             "actual_intents", "actual_submitted", "actual_filled"])
        assert all(v == FAILED + ";" + C.KEPT_LAST_GOOD
                   for v in s["unavailable"].values())
        # the ratios are from the values written (evaluated 6 -> decided 4)
        assert s["ratios"]["evaluated->decided"]["ratio"] == round(4 / 6, 6)
        # the healthy run after it: everything fresh, final, nothing named
        await C.persist_day(conn, _funnel(
            league, provider_events=12, normalized_events=11,
            venue_discovered=9, mapped_events=8, settlement_supported=7,
            evaluated_events=6, decided_events=5, entered_events=3,
            refused_events=2, ordered_events=3, filled_events=2,
            actual_intents=3, actual_submitted=3, actual_filled=2,
            venue_catalogue_events=4), now=LATER)
        h = await _snap(conn, league)
        assert h["final"] is True and h["unavailable"] == {}
        assert (h["decided_events"], h["actual_intents"]) == (5, 3)
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_a_failed_read_while_the_day_is_still_running_keeps_the_value_too():
    """The same rule inside the day: a transient failure mid-day does not turn
    a measured number into NULL for a run (and the day is not near final)."""
    conn = await H.connect()
    try:
        await clean(conn)
        league = league_name()
        await C.persist_day(conn, _funnel(league, provider_events=4,
                                          evaluated_events=3),
                            now=BEFORE_MIDNIGHT - 3600.0)
        await C.persist_day(conn, _funnel(
            league, unavailable={"evaluated_events": FAILED},
            provider_events=5), now=BEFORE_MIDNIGHT)
        s = await _snap(conn, league)
        assert s["final"] is False
        assert (s["provider_events"], s["evaluated_events"]) == (5, 3)
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_an_absent_table_or_an_unrecorded_stage_is_not_a_failed_read():
    """SOURCE_TABLE_ABSENT and NOT_MEASURED_BY_THE_COLLECTION_LEDGER are what
    the source is on every run: the day finalises as before, the column NULL
    with its reason (never kept from an earlier run, never a zero)."""
    conn = await H.connect()
    try:
        await clean(conn)
        league = league_name()
        absent = C.R_TABLE_ABSENT + ":us_premap"
        await C.persist_day(conn, _funnel(
            league, venue_catalogue_events=3, provider_events=2),
            now=BEFORE_MIDNIGHT)
        f = _funnel(league, unavailable={
            "venue_catalogue_events": absent,
            "settlement_supported": C.R_SETTLEMENT_UNMEASURED},
            provider_events=2)
        await C.persist_day(conn, f, now=AFTER_MIDNIGHT)
        s = await _snap(conn, league)
        assert s["final"] is True
        assert s["venue_catalogue_events"] is None
        assert s["unavailable"]["venue_catalogue_events"] == absent
        assert s["settlement_supported"] is None
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_a_read_that_failed_for_one_source_holds_the_whole_day_open():
    """Any failed source keeps the day open, not only the stage in a ratio."""
    conn = await H.connect()
    try:
        await clean(conn)
        league = league_name()
        f = _funnel(league, unavailable={"venue_catalogue_events": FAILED},
                    provider_events=2)
        await C.persist_day(conn, f, now=LATEST)
        assert (await _snap(conn, league))["final"] is False
    finally:
        await clean(conn)
        await conn.close()


def test_the_marker_and_the_rule_are_pure():
    f = _funnel("x_league", unavailable={"evaluated_events": FAILED})
    assert C.failed_reads(f) == {"evaluated_events": FAILED}
    assert C.day_is_final(f, now=LATEST) is False
    # a day that was read whole is final once it has ended, not before
    ok = _funnel("x_league", provider_events=1)
    assert C.failed_reads(ok) == {}
    assert C.day_is_final(ok, now=AFTER_MIDNIGHT) is True
    assert C.day_is_final(ok, now=BEFORE_MIDNIGHT) is False
    # an absent table and an unrecorded stage are not failed reads
    inert = _funnel("x_league", unavailable={
        "venue_catalogue_events": C.R_TABLE_ABSENT + ":us_premap",
        "settlement_supported": C.R_SETTLEMENT_UNMEASURED})
    assert C.failed_reads(inert) == {}
    assert C.day_is_final(inert, now=AFTER_MIDNIGHT) is True
    # a failure in ANY league row's own unavailable also counts
    only_row = _funnel("x_league", unavailable={"evaluated_events": FAILED})
    only_row["unavailable"] = {}
    assert list(C.failed_reads(only_row)) == ["evaluated_events"]
    prior = {"final": False, "evaluated_events": 7}
    kept = C.keep_last_good(f["leagues"]["x_league"], prior)
    assert kept["evaluated_events"] == 7
    assert kept["unavailable"]["evaluated_events"] == \
        FAILED + ";" + C.KEPT_LAST_GOOD
    # the input row is not mutated
    assert f["leagues"]["x_league"]["evaluated_events"] is None
    # a FINAL prior row, or none, is never merged
    assert C.keep_last_good(f["leagues"]["x_league"],
                            {"final": True, "evaluated_events": 7}
                            )["evaluated_events"] is None
    assert C.keep_last_good(f["leagues"]["x_league"], None
                            )["evaluated_events"] is None
