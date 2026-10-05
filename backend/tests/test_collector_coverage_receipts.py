"""THE COLLECTOR'S COVERAGE RECEIPTS, THROUGH THE DATABASE (migration 248).

The scheduler (`collector_coverage`) is proved pure in
test_collector_coverage_scheduler.py. These tests prove the durable half:

  * migration 248's invariants: a deferral carries its slot, only the cycle
    row carries the budget, receipts are append-only (UPDATE, DELETE and
    TRUNCATE refused), and the rollback refuses while any receipt exists;
  * the scheduled cycle's writer persists one budget row per cycle, one row
    per competition and one per deferred provider event, records whether it
    held the collector's single-writer lease, and reads its own memory back
    (last served, 24 h spend, measured cost, a never-served competition's
    run of deferrals) -- so a restart resets nothing;
  * a multi-cycle Saturday driven THROUGH THE DATABASE, restarting the
    process memory every cycle, serves NCAAF every cycle alongside MLB, the
    NFL and soccer within the declared four calls;
  * coverage_integrity reads the same rows (league_status.collector), so the
    operations desk shows real budget drops with their reasons.

Every test runs in its own schema inside a transaction that is rolled back:
the receipts table is created there by the migration itself, so nothing the
other suites committed can leak in, and nothing here is left behind.
ALL DATA IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import pathlib
import time
import uuid

import asyncpg
import pytest

from sportsassets import collector_coverage as cov
from sportsassets.agents import coverage_integrity as C
from sportsassets.workers import ext_pinnacle_loop as loop
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "248_collector_coverage_receipts.sql").read_text()
DOWN = (MIG / "rollback" / "248_collector_coverage_receipts.down.sql"
        ).read_text()
CYCLE = loop.CYCLE_S
HOUR = 3600.0
NCAAF = "americanfootball_ncaaf"
NFL = "americanfootball_nfl"
MLB = "baseball_mlb"
UNL = "soccer_uefa_nations_league"
BRB = "soccer_brazil_serie_b"
USLC = "soccer_usa_usl_championship"


async def _isolated():
    """A connection inside a transaction, with a private schema first on the
    search path and migration 248 applied INTO it."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    schema = "t248_%s" % uuid.uuid4().hex[:10]
    await conn.execute("CREATE SCHEMA %s" % schema)
    await conn.execute("SET LOCAL search_path TO %s, public" % schema)
    await conn.execute(UP)
    return conn, tx, schema


async def _close(conn, tx):
    try:
        await tx.rollback()
    finally:
        await conn.close()


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


def _reset_memory():
    loop._COVERAGE.update(last_served={}, ledger=[], cost={},
                          deferred_events={}, demand_since={},
                          demand_known=False)


async def _take_lease(conn):
    """THE COLLECTOR'S SINGLE-WRITER LEASE on this connection -- the session
    advisory lock `run` takes before any cycle -- so what this connection
    writes is the collector's own receipts (writer_lease HELD)."""
    assert await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                               loop.LOCK_KEY), (
        "another session holds the collector's writer lock on the test "
        "database")


async def _drop_lease(conn):
    await conn.fetchval("SELECT pg_advisory_unlock($1)", loop.LOCK_KEY)


INSERT = ("INSERT INTO collector_coverage_receipts (cycle_id, cycle_at, "
          "writer_lease, scheduler_version, scope, competition, planned, "
          "receipt, next_slot_at, calls_budget, calls_made, credits_charged, "
          "credits_basis) VALUES ($1, now(), 'HELD', 'V', $2, $3, $4, $5, $6, "
          "$7, $8, $9, $10)")


# ═════════════════════════════════════════════════════════════════════
# 1 · MIGRATION 248
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_migration_248_states_its_invariants_in_the_schema():
    conn, tx, schema = await _isolated()
    try:
        await conn.execute(UP)                          # idempotent
        await conn.execute(INSERT, "c1", "CYCLE", "(cycle)", "CYCLE_BUDGET",
                           "CYCLE_BUDGET", None, 4, 3, 0.0, None)
        await conn.execute(INSERT, "c1", "COMPETITION", NCAAF, "SCHEDULED",
                           "FETCHED", None, None, None, 3.0,
                           cov.COST_MEASURED)
        # a deferral without its slot is a silent drop: refused
        await _expect(conn, asyncpg.CheckViolationError, INSERT, "c1",
                      "COMPETITION", UNL, "DEFERRED_TO_SLOT",
                      "DEFERRED_TO_SLOT", None, None, None, 0.0, None)
        # the slot must be later than the cycle
        await _expect(conn, asyncpg.CheckViolationError, INSERT, "c1",
                      "COMPETITION", BRB, "DEFERRED_TO_SLOT",
                      "DEFERRED_TO_SLOT", None, None, None, 0.0, None)
        # only the cycle row carries the budget, and it must
        await _expect(conn, asyncpg.CheckViolationError, INSERT, "c2",
                      "CYCLE", "(cycle)", "CYCLE_BUDGET", "CYCLE_BUDGET",
                      None, None, None, 0.0, None)
        await _expect(conn, asyncpg.CheckViolationError, INSERT, "c1",
                      "COMPETITION", MLB, "SCHEDULED", "FETCHED", None, 4, 1,
                      3.0, cov.COST_MEASURED)
        # spend on something never fetched is refused; so is unexplained
        # spend
        await _expect(conn, asyncpg.CheckViolationError, INSERT, "c1",
                      "COMPETITION", USLC, "SKIPPED_NO_VENUE_EVENT_IN_HORIZON",
                      "SKIPPED_NO_VENUE_EVENT_IN_HORIZON", None, None, None,
                      20.0, cov.COST_MEASURED)
        await _expect(conn, asyncpg.CheckViolationError, INSERT, "c1",
                      "COMPETITION", NFL, "SCHEDULED", "FETCHED", None, None,
                      None, 3.0, None)
        # one budget row and one row per competition per cycle
        await _expect(conn, asyncpg.UniqueViolationError, INSERT, "c1",
                      "CYCLE", "(cycle)", "CYCLE_BUDGET", "CYCLE_BUDGET",
                      None, 4, 1, 0.0, None)
        await _expect(conn, asyncpg.UniqueViolationError, INSERT, "c1",
                      "COMPETITION", NCAAF, "SCHEDULED", "FETCH_FAILED",
                      None, None, None, 0.0, None)
        # every row says whether its writer held the collector's lease, and
        # says it in one of three words
        await _expect(conn, asyncpg.NotNullViolationError,
                      INSERT.replace("'HELD'", "NULL"), "c1", "COMPETITION",
                      MLB, "SCHEDULED", "FETCHED", None, None, None, 3.0,
                      cov.COST_MEASURED)
        await _expect(conn, asyncpg.CheckViolationError,
                      INSERT.replace("'HELD'", "'MAYBE'"), "c1",
                      "COMPETITION", MLB, "SCHEDULED", "FETCHED", None, None,
                      None, 3.0, cov.COST_MEASURED)
        # APPEND-ONLY
        for sql in ("UPDATE collector_coverage_receipts SET why = 'x'",
                    "DELETE FROM collector_coverage_receipts",
                    "TRUNCATE collector_coverage_receipts"):
            await _expect(conn, asyncpg.RaiseError, sql)
        # the rollback refuses while any receipt exists
        await _expect(conn, asyncpg.RaiseError, DOWN)
    finally:
        await _close(conn, tx)


@pg
async def test_rollback_248_drops_only_its_objects_when_empty():
    conn, tx, schema = await _isolated()
    try:
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('%s.collector_coverage_receipts')"
            % schema) is None
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid = p.pronamespace WHERE n.nspname = $1 "
            "AND p.proname = 'collector_coverage_receipts_append_only'",
            schema) == 0
        await conn.execute(UP)                          # and re-applies
    finally:
        await _close(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# 2 · THE WRITER, THE LEASE AND THE MEMORY
# ═════════════════════════════════════════════════════════════════════

def _comps(now, *, last=None, since=None):
    last, since = last or {}, since or {}

    def c(key, fam, ev, start_h, feed, **kw):
        return cov.competition(
            key=key, family=fam, listed=True, active=True,
            events_in_horizon=ev, next_start=now + start_h * HOUR,
            feed_covered=feed, last_served_at=last.get(key),
            demand_since=since.get(key), **kw)
    return [c(NCAAF, "football", 60, 1.0, False),
            c(NFL, "football", 14, 26.0, False),
            c(MLB, "baseball", 4, 3.0, True, confirmed=True),
            c(UNL, "soccer", 3, 2.0, True),
            c(BRB, "soccer", 3, 5.0, True),
            c(USLC, "soccer", 10, 7.0, True),
            c("soccer_usa_mls", "soccer", 0, 60.0, True)]


@pg
async def test_the_writer_persists_the_cycle_and_reads_its_memory_back():
    conn, tx, schema = await _isolated()
    _reset_memory()
    try:
        now = time.time() - 2 * CYCLE
        plan = cov.plan(_comps(now), now=now, cycle_s=CYCLE)
        fetched = [k for k, _ in plan["fetch_order"]]
        assert len(fetched) == loop.MAX_METERED_SPORTS_PER_CYCLE == 4
        # one fetch measured, one failed, the rest at the estimate
        for i, key in enumerate(fetched):
            cov.settle(plan, key, ok=(i != 1), at=now + 1,
                       credits=3.0 if i == 0 else cov.CREDITS_PER_FETCH_ESTIMATE,
                       basis=(cov.COST_MEASURED if i == 0
                              else cov.COST_ESTIMATED))
        deferred = [r for r in plan["receipts"]
                    if r["planned"] == cov.DEFERRED_TO_SLOT]
        assert deferred, "the plan must defer something on this board"
        spend = 3.0 + 3 * cov.CREDITS_PER_FETCH_ESTIMATE

        # ── A CYCLE RUN WITHOUT THE COLLECTOR'S LEASE (a harness, a one-off
        # run): recorded, by name, and NOT the collector's memory ──────────
        assert await loop._writer_lease(conn) == "NOT_HELD"
        stray = await loop._persist_coverage_receipts(
            conn, cycle_id="cyc-0", cycle_at=now - CYCLE, plan=plan)
        assert stray["ok"] is True and stray["writer_lease"] == "NOT_HELD"
        assert await conn.fetchval(
            "SELECT count(*) FROM collector_coverage_receipts WHERE "
            "cycle_id = 'cyc-0' AND writer_lease <> 'NOT_HELD'") == 0
        mem0 = await loop.coverage_memory(conn, now=time.time())
        assert mem0["writer_lease"] == "NOT_HELD"
        assert mem0["last_served"] == {} and mem0["spent_24h"] == 0
        assert mem0["measured_cost"] is None and mem0["demand_since"] == {}

        # ── THE COLLECTOR: the same writer, holding the lease ────────────
        await _take_lease(conn)
        try:
            got = await loop._persist_coverage_receipts(
                conn, cycle_id="cyc-1", cycle_at=now, plan=plan,
                candidates=[{"sport_key": NCAAF, "family": "football",
                             "token": "cfb", "event_id": "evt-9",
                             "queue_position": 41, "feed_covered": False,
                             "commence_epoch": now + HOUR,
                             "next_slot_at": now + CYCLE,
                             "deferred_since": now, "why": "BOUND"}])
            assert got["ok"] is True, got
            assert got["rows"] == 1 + len(plan["receipts"]) + 1
            assert got["writer_lease"] == "HELD"
            assert await conn.fetchval(
                "SELECT count(*) FROM collector_coverage_receipts WHERE "
                "cycle_id = 'cyc-1' AND writer_lease <> 'HELD'") == 0
            cyc = await conn.fetchrow(
                "SELECT * FROM collector_coverage_receipts "
                "WHERE scope = 'CYCLE' AND cycle_id = 'cyc-1'")
            assert (cyc["calls_budget"], cyc["calls_made"]) == (4, 4)
            assert cyc["credits_spent"] == spend
            rows = {r["competition"]: r for r in await conn.fetch(
                "SELECT * FROM collector_coverage_receipts "
                "WHERE scope = 'COMPETITION' AND cycle_id = 'cyc-1'")}
            assert rows[fetched[0]]["receipt"] == "FETCHED"
            assert rows[fetched[0]]["credits_basis"] == cov.COST_MEASURED
            assert rows[fetched[1]]["receipt"] == "FETCH_FAILED"
            for r in deferred:
                row = rows[r["key"]]
                assert row["receipt"] == "DEFERRED_TO_SLOT"
                assert row["next_slot_at"].timestamp() > now
                assert "metered calls" in row["why"]
            assert rows["soccer_usa_mls"]["receipt"] == \
                "SKIPPED_NO_VENUE_EVENT_IN_HORIZON"
            cand = await conn.fetchrow(
                "SELECT * FROM collector_coverage_receipts "
                "WHERE scope = 'CANDIDATE'")
            assert cand["provider_event_id"] == "evt-9"

            # THE MEMORY, READ BACK FROM THE RECEIPTS (a fresh process on the
            # lease): the collector's cycle only -- cyc-0's identical spend is
            # not in it
            _reset_memory()
            mem = await loop.coverage_memory(conn, now=time.time())
            assert mem["source"] == "RECEIPTS"
            assert mem["writer_lease"] == "HELD"
            # a failed fetch had its turn: it is LAST SERVED for the schedule
            assert set(mem["last_served"]) == set(fetched)
            assert all(abs(v - now) < 1.0
                       for v in mem["last_served"].values())
            assert mem["spent_24h"] == pytest.approx(spend)
            assert mem["measured_cost"] == 3.0
            # every competition in demand resumes its run in demand
            in_demand = {r["key"] for r in plan["receipts"]
                         if r.get("demand_since") is not None}
            assert in_demand and in_demand == set(mem["demand_since"])
        finally:
            await _drop_lease(conn)

        # A READER WITHOUT THE LEASE STILL SEES THE COLLECTOR'S SPEND: it is
        # never told the envelope is emptier than the collector made it
        _reset_memory()
        mem2 = await loop.coverage_memory(conn, now=time.time())
        assert mem2["writer_lease"] == "NOT_HELD"
        assert mem2["spent_24h"] == pytest.approx(spend)
        assert set(mem2["last_served"]) == set(fetched)
    finally:
        _reset_memory()
        await _close(conn, tx)


@pg
async def test_no_table_is_named_never_a_silent_success():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        schema = "t248_%s" % uuid.uuid4().hex[:10]
        await conn.execute("CREATE SCHEMA %s" % schema)
        # a search path with NO receipts table on it
        await conn.execute("SET LOCAL search_path TO %s" % schema)
        plan = cov.plan(_comps(time.time()), now=time.time(), cycle_s=CYCLE)
        got = await loop._persist_coverage_receipts(
            conn, cycle_id="x", cycle_at=time.time(), plan=plan)
        assert got == {"ok": False, "rows": 0,
                       "refusal": "COVERAGE_RECEIPTS_TABLE_ABSENT",
                       "why": "migration 248 is not applied here"}
        rec = await C.collector_receipts(conn, now=time.time())
        assert rec["read"] is False
        assert rec["why"] == "MIGRATION_248_NOT_APPLIED"
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · A SATURDAY, THROUGH THE DATABASE, RESTARTING EVERY CYCLE
# ═════════════════════════════════════════════════════════════════════

def _slate(base):
    """(key, family, feed_covered, starts) -- a Saturday shaped on
    production's 2026-10-03 board, 100 cfb games from base + 1 h."""
    def spread(first, n, hours):
        return [first + i * hours * HOUR / max(1, n - 1) for i in range(n)]
    return [(NCAAF, "football", False, spread(base + HOUR, 100, 12.0)),
            (NFL, "football", False, spread(base + 22 * HOUR, 14, 7.0)),
            (MLB, "baseball", True, spread(base + 2 * HOUR, 4, 10.0)),
            (UNL, "soccer", True, spread(base - 1 * HOUR, 3, 6.0)),
            (BRB, "soccer", True, spread(base + 4 * HOUR, 3, 4.0)),
            (USLC, "soccer", True, spread(base + 8 * HOUR, 10, 8.0))]


def _horizon(starts, now):
    alive = [s for s in starts if s > now - cov.HORIZON_BEHIND_S]
    inh = [s for s in alive if s <= now + cov.HORIZON_AHEAD_S]
    return len(inh), (min(alive) if alive else None)


@pg
async def test_a_saturday_through_the_database_serves_ncaaf_every_cycle():
    """Twelve cycles (three hours) ending now. Each cycle builds its plan
    ONLY from what the receipts table says (the process memory is wiped
    before every cycle: a restart each time), persists it, and the next
    cycle reads it back. NCAAF is fetched in every cycle; MLB, the NFL and
    the soccer competitions alongside it; never more than four calls; and
    coverage_integrity's reader reports exactly that. The cycles run on the
    collector's single-writer lease, as `run` drives them."""
    conn, tx, schema = await _isolated()
    n = 12
    t_end = time.time() - 5.0
    base = t_end - (n - 1) * CYCLE
    slate = _slate(base)
    await _take_lease(conn)
    try:
        for k in range(n):
            now = base + k * CYCLE
            _reset_memory()                         # a restart, every cycle
            mem = await loop.coverage_memory(conn, now=now)
            comps = []
            for key, fam, feed, starts in slate:
                ev, nxt = _horizon(starts, now)
                comps.append(cov.competition(
                    key=key, family=fam, listed=True, active=True,
                    confirmed=(key == MLB), events_in_horizon=ev,
                    next_start=nxt, feed_covered=feed,
                    last_served_at=mem["last_served"].get(key),
                    demand_since=mem["demand_since"].get(key)))
            plan = cov.plan(comps, now=now, cycle_s=CYCLE,
                            spent_24h=mem["spent_24h"],
                            spend_ledger=mem["ledger"],
                            cost_per_fetch=(mem["measured_cost"]
                                            or cov.CREDITS_PER_FETCH_ESTIMATE),
                            cost_basis=(cov.COST_MEASURED
                                        if mem["measured_cost"]
                                        else cov.COST_ESTIMATED),
                            max_calls=loop.MAX_METERED_SPORTS_PER_CYCLE)
            got = [k_ for k_, _ in plan["fetch_order"]]
            assert NCAAF in got, (k, plan["receipts"])
            assert len(got) <= loop.MAX_METERED_SPORTS_PER_CYCLE
            for key in got:
                cov.settle(plan, key, ok=True, at=now + 1.0, credits=3.0,
                           basis=cov.COST_MEASURED)
            w = await loop._persist_coverage_receipts(
                conn, cycle_id="sat-%02d" % k, cycle_at=now, plan=plan)
            assert w["ok"], w
        rec = await C.collector_receipts(conn, now=t_end + 1.0)
        assert rec["read"] is True
        by = rec["by_competition"]
        assert by[NCAAF]["served"] == n and by[NCAAF]["budget_dropped"] == 0
        assert by[NCAAF]["cycles_since_served"] == 1
        for key in (MLB, UNL, BRB, USLC):
            # feed-covered: bound two cycles -> served at least every other
            assert by[key]["served"] >= n // 2, (key, by[key])
        # the NFL's slate enters the horizon at 22 h - 24 h < 0: in demand
        assert by[NFL]["served"] >= n // 2 + 1, by[NFL]
        cyc = rec["cycles"]
        assert cyc["cycles"] == n and cyc["over_budget"] == 0
        assert cyc["max_calls_made"] <= 4 == cyc["calls_budget"]
        assert cyc["writer_lease"] == "HELD"
        assert rec["cycles_without_lease"] == 0
    finally:
        _reset_memory()
        await _drop_lease(conn)
        await _close(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# 4 · THE OPERATIONS DESK READS REAL BUDGET DROPS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_league_status_names_the_budget_drop_from_the_receipts():
    """Overload on purpose -- six metered-dependent competitions, four calls
    -- persisted for six cycles: coverage_integrity's collector reads the
    receipts (not the heartbeat), names which competitions the latest cycle
    budget-dropped with the reason and the promised slot, and a league with
    no provider events today says how often it was dropped. A cycle run
    WITHOUT the collector's lease in the same window is counted apart and
    changes none of it."""
    conn, tx, schema = await _isolated()
    n = 6
    t_end = time.time() - 5.0
    base = t_end - (n - 1) * CYCLE
    keys = [NCAAF, NFL, "americanfootball_x1", "americanfootball_x2",
            "americanfootball_x3", "americanfootball_x4"]
    last, since = {}, {}
    try:
        await _take_lease(conn)
        for k in range(n):
            now = base + k * CYCLE
            comps = [cov.competition(key=key, family="football",
                                     listed=True, active=True,
                                     events_in_horizon=5,
                                     next_start=now + (i + 1) * HOUR,
                                     last_served_at=last.get(key),
                                     demand_since=since.get(key))
                     for i, key in enumerate(keys)]
            plan = cov.plan(comps, now=now, cycle_s=CYCLE)
            assert plan["feasible"] is False
            for key, _ in plan["fetch_order"]:
                cov.settle(plan, key, ok=True, at=now + 1, credits=3.0,
                           basis=cov.COST_MEASURED)
                last[key] = now
            since = {r["key"]: r["demand_since"] for r in plan["receipts"]
                     if r.get("demand_since") is not None}
            await loop._persist_coverage_receipts(
                conn, cycle_id="ovl-%d" % k, cycle_at=now, plan=plan)
        await _drop_lease(conn)
        dropped_last = [r["key"] for r in plan["receipts"]
                        if r["planned"] == cov.DEFERRED_TO_SLOT]
        assert len(dropped_last) == 2
        # A STRAY, LEASE-LESS CYCLE inside the window that fetched the two
        # the collector dropped: recorded, and never read as the collector's
        stray = cov.plan([cov.competition(key=key, family="football",
                                          listed=True, active=True,
                                          events_in_horizon=5,
                                          next_start=t_end + HOUR)
                          for key in dropped_last], now=t_end - 1.0,
                         cycle_s=CYCLE)
        for key, _ in stray["fetch_order"]:
            cov.settle(stray, key, ok=True, at=t_end, credits=3.0,
                       basis=cov.COST_MEASURED)
        w = await loop._persist_coverage_receipts(
            conn, cycle_id="stray", cycle_at=t_end - 1.0, plan=stray)
        assert w["ok"] and w["writer_lease"] == "NOT_HELD"
        coll = await C.collector_selection(conn, now=t_end + 1.0)
        assert coll["receipts"]["cycles_without_lease"] == 1
        assert coll["source"] == "COVERAGE_RECEIPTS" and coll["fresh"]
        assert sorted(coll["budget_dropped"]) == sorted(dropped_last)
        assert coll["budget"] == 4
        for key in dropped_last:
            why = coll["budget_dropped_why"][key]
            assert why["receipt"] == "DEFERRED_TO_SLOT"
            assert why["next_slot_at"] > t_end
        # every competition was served within its starvation bound
        by = coll["receipts"]["by_competition"]
        for key in keys:
            assert by[key]["served"] >= n // 2, (key, by[key])
            assert by[key]["cycles"] == n, (key, by[key])
            # R30A fix stage: the stated overload bound counts from the
            # largest staleness bound a competition can carry (two): its own
            # bound can relax mid-run (adversarial review, finding 4)
            assert by[key]["starvation_bound_cycles"] == \
                cov.BOUND_CYCLES_MAX + -(-(len(keys) - 1) // 4)
        assert coll["receipts"]["cycles"]["cycles"] == n
        # the league status of a dropped league with no provider events
        league = dropped_last[0]
        row = {C.COLUMN[s]: 0 for s in C.STAGES}
        row.update({c: 0 for c in C.EXTRA_COLUMNS})
        row.update(league=league, unavailable={}, provider_events=0,
                   venue_catalogue_events=5)
        st = C.classify_status(row, scope={"in_scope": True},
                               collector=coll)
        assert st["status"] == C.S_UNAVAILABLE
        assert "NOT_REQUESTED_METERED_BUDGET_SPENT" in st["reason"]
        assert "metered calls" in st["reason"]
        assert "budget-dropped in %d of %d cycles in 24 h" % (
            by[league]["budget_dropped"], n) in st["reason"]
        assert "venue lists 5" in st["reason"]
        # the table the desk renders carries the receipts per league
        table = await C.league_status_table(
            conn, rows=[dict(row, league=NCAAF)],
            day=C.local_day(t_end, "UTC"), tz="UTC", now=t_end + 1.0)
        ncaaf = next(s for s in table["statuses"] if s["league"] == NCAAF)
        assert ncaaf["collector_receipt"]["cycles"] == n
        assert table["collector"]["receipts"]["cycles"]["over_budget"] == 0
        assert table["collector"]["source"] == "COVERAGE_RECEIPTS"
        assert table["collector"]["receipts"]["cycles_without_lease"] == 1
    finally:
        await _drop_lease(conn)
        await _close(conn, tx)


@pg
async def test_a_skipped_league_says_it_had_no_venue_event_in_the_horizon():
    conn, tx, schema = await _isolated()
    try:
        now = time.time() - 5.0
        plan = cov.plan([cov.competition(
            key=BRB, family="soccer", listed=True, active=True,
            events_in_horizon=0, next_start=now + 40 * HOUR,
            feed_covered=True)], now=now, cycle_s=CYCLE)
        await _take_lease(conn)
        await loop._persist_coverage_receipts(conn, cycle_id="s1",
                                              cycle_at=now, plan=plan)
        await _drop_lease(conn)
        coll = await C.collector_selection(conn, now=now + 1.0)
        row = {C.COLUMN[s]: 0 for s in C.STAGES}
        row.update({c: 0 for c in C.EXTRA_COLUMNS})
        row.update(league=BRB, unavailable={}, provider_events=0,
                   venue_catalogue_events=2)
        st = C.classify_status(row, scope={"in_scope": True},
                               collector=coll)
        assert "NOT_REQUESTED_NO_VENUE_EVENT_IN_THE_COLLECTOR_HORIZON" in \
            st["reason"], st
    finally:
        await _close(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# 5 · THE HORIZON READ AND THE SINGLE WRITER
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_horizon_read_counts_the_venue_slate_by_token():
    conn, tx, schema = await _isolated()
    try:
        await conn.execute(
            "CREATE TABLE %s.us_premap (LIKE public.us_premap INCLUDING ALL)"
            % schema)
        now = float(await conn.fetchval("SELECT extract(epoch FROM now())"))
        rows = [("aec-cfb-a-b-1", "cfb-a-b", "A vs B", "football_team_full_"
                 "game_winner", now + 2 * HOUR),
                ("aec-cfb-c-d-1", "cfb-c-d", "C vs D", "football_team_full_"
                 "game_winner", now + 30 * HOUR),
                ("aec-cfb-e-f-1", "cfb-e-f", "E vs F", "football_team_full_"
                 "game_winner", now - 2 * HOUR),
                ("aec-cfb-g-h-1", "cfb-g-h", "G vs H", "football_team_full_"
                 "game_winner", now - 10 * HOUR),
                ("aec-brb-i-j-1", "brb-i-j", "eBattles I vs J",
                 "soccer_team_full_time_winner", now + HOUR)]
        for i, (ms, es, title, st, gs) in enumerate(rows):
            await conn.execute(
                "INSERT INTO us_premap (identifier, market_slug, event_slug, "
                "event_title, question, sports_type, game_start) VALUES "
                "($1, $2, $3, $4, $4, $5, to_timestamp($6))",
                "id%d" % i, ms, es, title, st, gs)
        # listings with NO stated start: one the sweep refreshed now (start
        # unknown, counted apart) and one no sweep has touched for 3 days
        # (no longer listed: not counted)
        for ident, ms, es, age_h in (
                ("idn1", "aec-mlb-k-l-1", "mlb-k-l", 0.0),
                ("idn2", "aec-mlb-m-n-1", "mlb-m-n", 72.0)):
            await conn.execute(
                "INSERT INTO us_premap (identifier, market_slug, event_slug, "
                "event_title, question, sports_type, game_start, updated_at) "
                "VALUES ($1, $2, $3, 'K vs L', 'K vs L', "
                "'baseball_team_full_game_winner', NULL, "
                "now() - make_interval(secs => $4))",
                ident, ms, es, age_h * HOUR)
        got = await loop.venue_horizon(conn)
        assert got["read"] is True
        cfb = got["by_token"]["cfb"]
        # in the horizon: the one at +2 h and the one in play (-2 h); the
        # +30 h game is listed but not in it; -10 h is past the tail
        assert cfb["events_in_horizon"] == 2 and cfb["board_events"] == 3
        assert cfb["start_unknown"] == 0
        assert cfb["next_start"] == pytest.approx(now - 2 * HOUR, abs=1)
        mlb = got["by_token"]["mlb"]
        assert (mlb["events_in_horizon"], mlb["board_events"],
                mlb["start_unknown"], mlb["next_start"]) == (0, 0, 1, None)
        # the simulated competition is excluded by the venue's own words,
        # exactly as the board excludes it
        assert "brb" not in got["by_token"]
    finally:
        await _close(conn, tx)


def test_the_horizon_and_the_board_share_one_realism_rule():
    types, prose = loop._realism_exclusions()
    board = loop._board_sql()
    for clause in types.split("\n") + prose.split("\n"):
        clause = clause.strip()
        if clause:
            assert clause in board, clause
            assert clause in loop.VENUE_HORIZON_SQL, clause


def test_only_the_scheduled_cycle_writes_receipts():
    """The reactive (stream-seed) evaluations run `cycle` on pool
    connections without the writer lease: they never plan, never spend a
    metered call and never write a coverage receipt."""
    import inspect
    src = inspect.getsource(loop.cycle)
    seed_branch = src[src.index("if stream_seed is not None:"):
                      src.index("_cat = await fetch_sport_catalogue")]
    assert "coverage = None" in seed_branch
    assert "if coverage is not None:" in src
    i = src.index("coverage_receipts = await _persist_coverage_receipts(")
    assert "if coverage is not None:" in src[i - 400:i]


# ═════════════════════════════════════════════════════════════════════
# 6 · REPAIRS AFTER THE ADVERSARIAL REVIEW (each test failed first)
# ═════════════════════════════════════════════════════════════════════

#: Six competitions the real token maps carry, all METERED-DEPENDENT (the
#: feed is stubbed as covering nothing): demand of six one-cycle bounds
#: against four calls, so every cycle must spend all four.
WIRED = [("baseball_mlb", "mlb"), (NCAAF, "cfb"), (NFL, "nfl"),
         (UNL, "unl"), (BRB, "brb"), (USLC, "uslc")]


def _wire_stubs(monkeypatch, *, start_offset_h=2.0):
    """The scheduler's reads that need a venue catalogue, the held watch
    and the feed owner, stubbed so `plan_coverage` runs for real on its
    memory: every token has nine venue events in the horizon, nothing is
    held, and the feed covers no family."""
    async def _horizon(conn):
        return {"read": True, "source": "stub",
                "by_token": {tok: {"events_in_horizon": 9, "board_events": 9,
                                   "start_unknown": 0,
                                   "next_start": _horizon.now
                                   + start_offset_h * HOUR}
                             for _, tok in WIRED}}
    _horizon.now = 0.0

    async def _held(conn, **kw):
        return {"read": True, "by_key": {}, "unmapped": {},
                "finished_by_key": {}}

    monkeypatch.setattr(loop, "venue_horizon", _horizon)
    monkeypatch.setattr(loop, "held_competitions", _held)
    monkeypatch.setattr(loop, "feed_coverage", lambda: {"families": []})
    return _horizon


def _wired_inputs():
    catalogue = {"ok": True, "sports": [{"key": k, "active": True}
                                        for k, _ in WIRED]}
    selection = {"confirmed_by_provider": [
        {"key": k, "our_token": t} for k, t in WIRED
        if k != "baseball_mlb"], "rejected": []}
    return catalogue, selection


class _LeaseHolderNoTable:
    """A connection that holds the collector's writer lease on a database
    without migration 248: the in-process memory is the only memory."""
    async def fetchval(self, sql, *a):
        if "pg_locks" in sql:
            return True
        if "to_regclass" in sql:
            return False
        raise AssertionError(sql)


@pytest.mark.parametrize("overhead_s", [0.0, 0.05, 2.0])
async def test_the_spend_ledger_runs_on_the_cycle_clock(monkeypatch,
                                                        overhead_s):
    """FINDING 2. The in-process ledger stamped each fetch when it FINISHED
    while the rolling day is measured from the cycle START, so at the
    production cadence (900 s start to start) the day held 96 cycles of
    fetches, not 95: about once a day a cycle saw the whole envelope spent,
    scheduled nothing -- held positions included -- and blamed
    'higher-ranked competitions'. Driven here through the REAL wiring --
    `plan_coverage` on the lease holder's memory, `_record_spend` as the
    cycle calls it, fetch instants spread through the cycle -- for two and a
    half days: every cycle spends its four calls."""
    _reset_memory()
    horizon = _wire_stubs(monkeypatch)
    catalogue, selection = _wired_inputs()
    conn = _LeaseHolderNoTable()
    t0 = 1791014400.0
    try:
        for k in range(240):
            started = t0 + k * (CYCLE + overhead_s)
            horizon.now = started
            plan = await loop.plan_coverage(conn, catalogue=catalogue,
                                            selection=selection, now=started)
            assert plan.get("failed") is None, plan
            assert plan["calls"]["scheduled"] == 4, (
                k, plan["envelope"], [r["why"] for r in plan["receipts"]
                                      if r["planned"] != cov.SCHEDULED][:2])
            for i, (key, _) in enumerate(plan["fetch_order"]):
                loop._record_spend(key, at=started + 5.0 + 150.0 * i,
                                   cycle_at=started,
                                   credits=cov.CREDITS_PER_FETCH_ESTIMATE,
                                   basis=cov.COST_ESTIMATED)
            assert plan["envelope"]["spent_24h"] <= \
                cov.DAILY_CREDIT_ENVELOPE - 80.0 + 1e-9
    finally:
        _reset_memory()


@pg
async def test_the_receipts_memory_is_read_on_the_cycle_clock():
    """FINDING 2, the durable half. The receipts were windowed on the
    database's now(), the plan on the cycle's own instant -- two clocks for
    one rolling day. Both now use the cycle's instant, so a cycle (and a
    test replaying a past Saturday) reads exactly the day before it."""
    conn, tx, schema = await _isolated()
    _reset_memory()
    await _take_lease(conn)
    try:
        base = 1791014400.0                       # 2026-10-03T08:00Z
        for k in range(96):
            now = base + k * CYCLE
            plan = cov.plan([cov.competition(
                key=NCAAF, family="football", listed=True, active=True,
                events_in_horizon=9, next_start=now + HOUR)], now=now,
                cycle_s=CYCLE)
            cov.settle(plan, NCAAF, ok=True, at=now + 3.0, credits=20.0,
                       basis=cov.COST_ESTIMATED)
            w = await loop._persist_coverage_receipts(
                conn, cycle_id="clk-%03d" % k, cycle_at=now, plan=plan)
            assert w["ok"], w
        _reset_memory()                           # a restart
        now = base + 96 * CYCLE
        mem = await loop.coverage_memory(conn, now=now)
        assert mem["source"] == "RECEIPTS"
        # the 95 cycles before this one: cycle 0 is exactly a day old and
        # out of the half-open day
        assert mem["spent_24h"] == pytest.approx(95 * 20.0)
        assert mem["last_served"][NCAAF] == pytest.approx(base + 95 * CYCLE,
                                                          abs=0.01)
    finally:
        _reset_memory()
        await _drop_lease(conn)
        await _close(conn, tx)


@pg
async def test_plan_coverage_over_many_cycles_through_the_database(
        monkeypatch):
    """FINDING 2 (the untested wiring). The database-backed Saturday rebuilt
    its plan by hand; nothing drove `plan_coverage` and the lease holder's
    memory across many cycles. Here 110 cycles run through the real
    `plan_coverage` -> settle -> `_record_spend` -> `_persist_coverage_
    receipts` path on the lease, with the process memory kept (as `run`
    keeps it) for 100 cycles -- past the first full day -- then wiped (a
    restart): every cycle spends its four calls, the memory read back from
    the receipts after the restart is the memory the process had, and no
    rolling day ever exceeds the envelope."""
    conn, tx, schema = await _isolated()
    _reset_memory()
    horizon = _wire_stubs(monkeypatch)
    catalogue, selection = _wired_inputs()
    await _take_lease(conn)
    try:
        base = 1791014400.0
        for k in range(110):
            started = base + k * (CYCLE + 0.05)
            horizon.now = started
            if k == 100:
                before = await loop.coverage_memory(conn, now=started)
                _reset_memory()                   # a restart
                after = await loop.coverage_memory(conn, now=started)
                assert after["source"] == "RECEIPTS"
                assert after["spent_24h"] == pytest.approx(
                    before["spent_24h"])
                assert after["last_served"] == pytest.approx(
                    before["last_served"])
            plan = await loop.plan_coverage(conn, catalogue=catalogue,
                                            selection=selection, now=started)
            assert plan.get("failed") is None, plan
            assert plan["inputs"]["writer_lease"] == "HELD"
            assert plan["calls"]["scheduled"] == 4, (k, plan["envelope"])
            for i, (key, _) in enumerate(plan["fetch_order"]):
                spent, basis = loop._charge({"credits_last": None})
                cov.settle(plan, key, ok=True, at=started + 5.0 + 150 * i,
                           credits=spent, basis=basis)
                loop._record_spend(key, at=started + 5.0 + 150 * i,
                                   cycle_at=started, credits=spent,
                                   basis=basis)
            w = await loop._persist_coverage_receipts(
                conn, cycle_id="wire-%03d" % k, cycle_at=started, plan=plan)
            assert w["ok"], w
        days = await conn.fetch(
            "SELECT a.cycle_at, (SELECT sum(credits_spent) FROM "
            "collector_coverage_receipts b WHERE b.scope = 'CYCLE' AND "
            "b.cycle_at > a.cycle_at - interval '24 hours' AND b.cycle_at "
            "<= a.cycle_at) AS day FROM collector_coverage_receipts a "
            "WHERE a.scope = 'CYCLE'")
        assert max(float(r["day"]) for r in days) <= \
            cov.DAILY_CREDIT_ENVELOPE + 1e-9
    finally:
        _reset_memory()
        await _drop_lease(conn)
        await _close(conn, tx)


async def test_a_discovery_refresh_without_venue_titles_is_refused(
        monkeypatch):
    """FINDING 3. The discovery refresh ran the competition fixture check
    only when the candidate carried venue titles, so a candidate built from
    the board snapshot (which carries none) registered reactive seeds
    UNCONFIRMED -- the odds path refuses the same candidate
    (THE_MAPPING_COULD_NOT_BE_CONFIRMED_AGAINST_ANY_FIXTURE). The refresh
    now holds every candidate to the same confirmation."""
    from sportsassets import pinnapi_reactive as reactive
    _reset_memory()
    registered = []
    monkeypatch.setattr(reactive, "ACTIVE", object())
    monkeypatch.setattr(reactive, "register",
                        lambda e, **kw: registered.append(e.get("id")))
    now = 1791014400.0

    async def _events(key, *, api_key, timeout=20.0):
        return {"ok": True, "status": 200, "credits_last": 0.0,
                "received_at": now,
                "events": [{"id": "e1", "home_team": "Alpha FC",
                            "away_team": "Beta FC",
                            "commence_time": "2026-10-03T10:00:00Z"}]}
    monkeypatch.setattr(loop, "fetch_events", _events)

    def _plan():
        return {"receipts": [{"key": UNL, "family": "soccer",
                              "planned": cov.DEFERRED_TO_SLOT,
                              "feed_covered": True, "events_in_horizon": 3}],
                "envelope": {"cycle_budget": 80.0, "planned_spend": 0.0},
                "calls": {"budget": 4, "scheduled": 0, "extra": {}},
                "inputs": {"writer_lease": "HELD"}}
    try:
        # NO venue titles (the snapshot board): refused, nothing registered
        plan = _plan()
        out = await loop.refresh_unmetered_discovery(
            plan, {"confirmed_by_provider": [
                {"key": UNL, "our_token": "unl", "venue_titles": []}]},
            api_key="k", now=now)
        r = plan["receipts"][0]
        assert r["discovery"].startswith("DISCOVERY_REFRESH_REFUSED:"), r
        assert registered == [] and out["competitions"] == {}
        # titles that do NOT match the provider's fixtures: refused the same
        plan = _plan()
        await loop.refresh_unmetered_discovery(
            plan, {"confirmed_by_provider": [
                {"key": UNL, "our_token": "unl",
                 "venue_titles": ["Gamma vs Delta", "Epsilon vs Zeta"]}]},
            api_key="k", now=now)
        assert plan["receipts"][0]["discovery"].startswith(
            "DISCOVERY_REFRESH_REFUSED:")
        assert registered == []
    finally:
        _reset_memory()


async def test_discovery_spend_is_stamped_on_the_cycle_clock(monkeypatch):
    """FINDING 2. The discovery refresh and the re-fetch appended their spend
    at time.time(), the clock the rolling day is NOT measured on."""
    from sportsassets import pinnapi_reactive as reactive
    _reset_memory()
    monkeypatch.setattr(reactive, "ACTIVE", object())
    monkeypatch.setattr(reactive, "register", lambda e, **kw: None)
    now = 1791014400.0

    async def _events(key, *, api_key, timeout=20.0):
        return {"ok": False, "status": 500, "credits_last": 2.0,
                "received_at": now, "events": []}
    monkeypatch.setattr(loop, "fetch_events", _events)
    plan = {"receipts": [{"key": UNL, "family": "soccer",
                          "planned": cov.DEFERRED_TO_SLOT,
                          "feed_covered": True, "events_in_horizon": 3}],
            "envelope": {"cycle_budget": 80.0, "planned_spend": 0.0},
            "calls": {"budget": 4, "scheduled": 0, "extra": {}},
            "inputs": {"writer_lease": "HELD"}}
    try:
        await loop.refresh_unmetered_discovery(
            plan, {"confirmed_by_provider": []}, api_key="k", now=now)
        assert loop._COVERAGE["ledger"] == [(now, 2.0)]
        # the re-fetch's spend, through the helper the cycle calls
        coverage = cov.plan([cov.competition(
            key=NCAAF, family="football", listed=True, active=True,
            events_in_horizon=9, next_start=now + HOUR)], now=now,
            cycle_s=CYCLE)
        coverage["inputs"] = {"writer_lease": "HELD"}
        loop._charge_refetch(coverage, NCAAF, {"credits_last": 3.0},
                             cycle_at=now)
        assert loop._COVERAGE["ledger"][-1] == (now, 3.0)
        r = next(r for r in coverage["receipts"] if r["key"] == NCAAF)
        assert r["credits_charged"] == 3.0
        assert r["detail"]["refetches"] == 1
    finally:
        _reset_memory()


class _HeldConn:
    """The held watch's query and the venue's start times, for
    `held_competitions`."""
    def __init__(self, now):
        self.now = now

    async def fetch(self, sql, *a):
        if "paper_fills" in sql:
            return [{"slug": "aec-cfb-ala-mspst-2026-10-03", "kind": "PAPER"},
                    {"slug": "aec-unl-esp-fra-2026-10-01", "kind": "PAPER"},
                    {"slug": "aec-mlb-nyy-bos-2026-10-03", "kind": "PAPER"},
                    {"slug": "aec-zzz-a-b-2026-10-03", "kind": "PAPER"}]
        if "us_premap" in sql:
            return [{"slug": "aec-cfb-ala-mspst-2026-10-03",
                     "game_start": self.now - 1 * HOUR},
                    {"slug": "aec-unl-esp-fra-2026-10-01",
                     "game_start": self.now - 50 * HOUR}]
        raise AssertionError(sql)


async def test_held_positions_on_finished_games_are_counted_apart():
    """FINDING 1, the read. 'Held' is any paper fill with a positive net
    quantity and no settlement row -- which includes games that FINISHED
    and wait to settle. Those are named apart and do not put their
    competition on the one-cycle held bound; a held game in play (or whose
    start the venue does not state) still does."""
    now = 1791014400.0
    got = await loop.held_competitions(_HeldConn(now), now=now)
    assert got["read"] is True
    assert got["by_key"] == {NCAAF: 1, "baseball_mlb": 1}
    assert got["finished_by_key"] == {UNL: 1}
    assert got["unmapped"] == {"zzz": 1}


def test_a_budget_deferral_stays_inside_dereks_mandate():
    """FINDING 5. The heartbeat's `requested` is this cycle's fetch order,
    and Derek's census builds its mandate from it -- so a competition the
    schedule DEFERRED this cycle (or skipped for want of an event in the
    horizon) was filed OUTSIDE_MANDATE:LEAGUE_NOT_IN_THE_REQUESTED_SET, a
    scope label on a scheduling decision, flickering cycle to cycle as the
    competitions rotate. The heartbeat now carries the schedule's rotation
    and the census reads it."""
    from sportsassets.agents import coverage as dcov
    now = 1791014400.0
    comps = [cov.competition(key=k, family="football" if "football" in k
                             else "soccer", listed=True, active=True,
                             events_in_horizon=5, next_start=now + HOUR)
             for k in (NFL, UNL, BRB, USLC, "soccer_usa_mls")]
    comps.append(cov.competition(key=NCAAF, family="football", listed=True,
                                 active=True, events_in_horizon=0,
                                 next_start=now + 30 * HOUR))
    plan = cov.plan(comps, now=now, cycle_s=CYCLE)
    deferred = [r["key"] for r in plan["receipts"]
                if r["planned"] == cov.DEFERRED_TO_SLOT]
    assert deferred
    sel = {"sports": plan["fetch_order"], "coverage": plan}
    sel.update(loop.coverage_selection_view(plan))
    digest = loop._selection_digest({"sports_selection": sel})
    assert set(digest["requested"]) == {k for k, _ in plan["fetch_order"]}
    rotation = set(digest["in_rotation"])
    assert set(deferred) <= rotation and NCAAF in rotation
    assert digest["schedule"][NCAAF] == cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON

    class _Conn:
        async def fetchval(self, sql, *a):
            import json
            return json.dumps({"at": now, "sports_selection": digest})
    import asyncio as _a
    req = _a.run(dcov.requested_set(_Conn(), now=now + 1.0))
    assert req["fresh"] is True
    assert set(req["keys"]) >= rotation
    mand = dcov.mandate(req["keys"])
    # an NCAAF listing (cfb), its competition outside the horizon this
    # cycle: not OUTSIDE_MANDATE by league
    assert "cfb" in mand["league_tokens_by_family"]["football"]
