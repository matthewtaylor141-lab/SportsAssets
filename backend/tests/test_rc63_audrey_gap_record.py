"""rc6.3: a completed day with paper activity and no Audrey report is named.

98fdc79d closes the day last reported, after any gap, and writes no report
for a day no pass reported. What it left: a day on which the paper pass ran
and the account had activity (decisions, orders, fills, settlements, Xavier
reviews) while Audrey's step never finished got NO report, and NOTHING
recorded that it was never reconciled; its activity only reached the
balances of the next reported day. And on the first deploy of day closing,
when a worker still on the code before it reached midnight first (writing the
new day's first version), the new code's first pass found no closed day and
the newest reported day not over, so the day before the deploy never got a
final version.

Proven against Postgres through the real step():
  * a completed day with fills and no report gets exactly one
    AUDREY_DAY_NOT_RECONCILED finding (WARNING, its day, window, activity
    counts and why, an improvement task) and no report row, final or
    otherwise; a gap day with no activity, a day that has a report (even one
    written only before the day's fills) and today get nothing;
  * the session's first day, when Audrey never reported it, is named too;
  * repeated passes, later day turns and two concurrent callers (forced to
    both see the day unrecorded) store one finding and one task, and exactly
    one caller reports it new;
  * a late version of the day landing while a pass looks at it wins: under
    the day's report lock the pass reads the day again and names nothing;
  * the paper Audrey read model counts these findings;
  * the new code's first pass after an old-code worker turned midnight closes
    the day before the deploy, the days before it stay history, and no
    history day is named (no backfill).
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_readmodel as RM
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import paper_audrey as PA

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

NY = "America/New_York"
T = H.T0                                   # 2026-09-24 21:33:20 New York
DAY0, START0, END0 = PA.day_bounds(T)
DAY = 86400.0
#: the finding's name, spelled out (not read from the module under test)
KIND = "AUDREY_DAY_NOT_RECONCILED"


def _day(k: int) -> dt.date:
    return DAY0 + dt.timedelta(days=k)


def _start(k: int) -> float:
    """New York midnight opening day k (no DST change before November)."""
    return START0 + k * DAY


def _ctx(a, now):
    return {"account_id": a["account_id"], "session_id": a["session_id"],
            "config": a["config"], "now": now, "clock": lambda: now,
            "session": {"session_id": a["session_id"], "config": a["config"],
                        "reporting_tz": NY},
            "fee_fn": H.zero_fee, "deadline": 1e18}


async def _fill(conn, a, key, at, *, qty=10, price=0.40) -> list:
    """A round trip the paper pass's simulator fills with no Audrey step: an
    entry at `at` and its exit 10 s later (two orders, two fills), so the
    position is closed and the next entry is not held by the stale-
    management rail."""
    slug = "%s:%s" % (a["account_id"], key)
    gid = "paper_g_%s_%s" % (a["account_id"][-10:], key)
    bid = round(price - 0.03, 2)
    oids = []
    for k, (side, role, limit, t) in enumerate((
            ("BUY", "ENTRY", price, at), ("SELL", "EXIT", bid, at + 10))):
        o = H.order(a, key="%s:%s" % (key, k), slug=slug, qty=qty,
                    limit=limit, at=t, group_id=gid, direction=side,
                    role=role)
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=t)
        assert got.get("order"), got
        oid = got["order"]["order_id"]
        await H.observe(conn, slug, t + 3.0, offers=[(price, qty)],
                        bids=[(bid, qty)])
        await SIM.simulate_order(conn, oid, now=t + 4.0, fee_fn=H.zero_fee)
        n = await conn.fetchval("SELECT count(*) FROM paper_fills WHERE "
                                " order_id=$1", oid)
        assert n == 1, (role, n)
        oids.append(oid)
    return oids


async def _reserve(conn, a, key, at):
    """A resting order: its reservation changes the report's balances."""
    o = H.order(a, key=key, qty=10, limit=0.40, at=at, ttl=21 * 86400,
                slug="%s:mkt" % a["account_id"], group_id="paper_g_%s_%s" % (
                    a["account_id"][-10:], key))
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got.get("order"), got


async def _days(conn, a) -> dict:
    """{report_day: [(version, final), ...]} of the session."""
    out: dict = {}
    for r in await conn.fetch(
            "SELECT report_day, version, final FROM paper_audrey_reports "
            " WHERE session_id=$1 ORDER BY report_day, version",
            a["session_id"]):
        out.setdefault(r["report_day"], []).append((r["version"], r["final"]))
    return out


async def _finals_per_day(conn, a) -> dict:
    return {r["report_day"]: int(r["n"]) for r in await conn.fetch(
        "SELECT report_day, count(*) FILTER (WHERE final) AS n "
        "  FROM paper_audrey_reports WHERE session_id=$1 GROUP BY 1",
        a["session_id"])}


async def _named(conn, a) -> list:
    """The session's AUDREY_DAY_NOT_RECONCILED findings, by day."""
    return await conn.fetch(
        "SELECT * FROM paper_audrey_findings WHERE session_id=$1 AND kind=$2"
        " ORDER BY subject", a["session_id"], KIND)


async def _tasks(conn, task_id) -> int:
    return int(await conn.fetchval(
        "SELECT count(*) FROM agent_tasks WHERE task_id=$1", task_id))


@pg
async def test_a_day_with_fills_and_no_report_is_named_once_with_no_report():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audrec", now=T)
        assert (await PA.step(conn, _ctx(a, T)))["report"]["version"] == 1
        # day 0: a fill after the day's only intra-day version -- the
        # closing version (whole day) covers it, so day 0 is not named
        await _fill(conn, a, "d0", END0 - 1800)
        # day 1: the paper pass ran (two round trips) but Audrey's step never
        # finished; day 2: no pass, no activity
        await _fill(conn, a, "d1a", _start(1) + 12 * 3600)
        await _fill(conn, a, "d1b", _start(1) + 15 * 3600)
        # day 3 (today): activity before Audrey resumes -- not over, so
        # never named
        await _fill(conn, a, "d3", _start(3) + 9 * 3600)
        resume = _start(3) + 10 * 3600
        got = await PA.step(conn, _ctx(a, resume))
        days = await _days(conn, a)
        # no report of any kind for the gap days; day 0 closed, day 3 open
        assert days == {DAY0: [(1, False), (2, True)], _day(3): [(1, False)]}
        assert [c["report_day"] for c in got["closing"]] == [str(DAY0)]
        # exactly one named finding: day 1 (fills), not day 2 (nothing)
        rows = await _named(conn, a)
        assert [r["subject"] for r in rows] == [str(_day(1))]
        f = rows[0]
        assert f["severity"] == "WARNING"
        d = H.j(f["detail"])
        assert d["day"] == str(_day(1)) and d["reporting_tz"] == NY
        assert d["window"] == {"start": _start(1), "end": _start(2)}
        act = d["activity"]
        assert set(act) == {"ledger_entries", "decisions", "orders", "fills",
                            "settlements", "xavier_reviews"}
        # the four orders and their fills; the ledger stamps committed_at
        # with the database clock, so a test's simulated day holds none
        assert (act["fills"], act["orders"]) == (4, 4)
        assert (act["decisions"], act["settlements"],
                act["xavier_reviews"]) == (0, 0, 0)
        assert d["activity_total"] == sum(act.values())
        assert d["report_versions"] == 0 and d["report_written"] is False
        assert d["looked_after_day"] == str(DAY0)
        assert d["looked_after_basis"] == "NEWEST_CLOSED_DAY"
        assert d["observed_at"] == resume
        assert "no Audrey report covers it" in d["why"]
        assert got["days_not_reconciled"] == 1
        # the improvement hook opened its one task, owned by Audrey
        assert f["improvement_task_id"]
        assert await _tasks(conn, f["improvement_task_id"]) == 1
        assert await conn.fetchval(
            "SELECT assignee FROM agent_tasks WHERE task_id=$1",
            f["improvement_task_id"]) == "AUDREY"
        # repeated passes, the next day turn and a pass after it
        for at in (resume + 60, resume + 60 + PA.REPORT_EVERY_S,
                   _start(4) + 30, _start(4) + 3600):
            again = await PA.step(conn, _ctx(a, at))
            assert again["days_not_reconciled"] == 0
        rows = await _named(conn, a)
        assert [r["finding_id"] for r in rows] == [f["finding_id"]]
        assert await _tasks(conn, f["improvement_task_id"]) == 1
        days = await _days(conn, a)
        assert _day(1) not in days and _day(2) not in days
        assert await _finals_per_day(conn, a) == {
            DAY0: 1, _day(3): 1, _day(4): 0}
        # the paper Audrey read model counts it
        rm = await RM.audrey_payload(conn, account_id=a["account_id"],
                                     now=_start(4) + 3600)
        nr = rm["days_not_reconciled"]
        assert nr["kind"] == KIND and nr["count"] == 1 and nr["why"] is None
        assert [(x["day"], x["session_id"]) for x in nr["days"]] == [
            (str(_day(1)), a["session_id"])]
        assert nr["days"][0]["activity_total"] == d["activity_total"]
        assert KIND in {e["kind"] for e in rm["audit_entries"]["data"]}
    finally:
        await conn.close()


@pg
async def test_a_day_with_no_activity_and_no_report_is_not_named():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audquiet", now=T)
        await PA.step(conn, _ctx(a, T))
        resume = _start(3) + 10 * 3600          # days 1 and 2: nothing
        got = await PA.step(conn, _ctx(a, resume))
        assert await _days(conn, a) == {DAY0: [(1, False), (2, True)],
                                        _day(3): [(1, False)]}
        assert got["days_not_reconciled"] == 0
        for at in (resume + 60 + PA.REPORT_EVERY_S, _start(4) + 30):
            assert (await PA.step(conn, _ctx(a, at)))[
                "days_not_reconciled"] == 0
        assert await _named(conn, a) == []
        rm = await RM.audrey_payload(conn, account_id=a["account_id"],
                                     now=_start(4) + 30)
        assert rm["days_not_reconciled"]["count"] == 0
        assert rm["days_not_reconciled"]["days"] == []
    finally:
        await conn.close()


@pg
async def test_the_sessions_first_day_with_fills_and_no_report_is_named():
    """Audrey's step never finished on the session's first day: no closed
    day and no reported day before today, so the days looked at start at
    the session's start."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audfirst", now=T)
        await _fill(conn, a, "f0", T + 60)
        got = await PA.step(conn, _ctx(a, _start(1) + 3600))
        assert await _days(conn, a) == {_day(1): [(1, False)]}
        rows = await _named(conn, a)
        assert [r["subject"] for r in rows] == [str(DAY0)]
        assert got["closing"] == [] and got["days_not_reconciled"] == 1
        d = H.j(rows[0]["detail"])
        assert d["looked_after_basis"] == "SESSION_START"
        assert (d["activity"]["fills"], d["activity"]["orders"]) == (2, 2)
        # the next turn closes day 1 and names nothing more
        turn = await PA.step(conn, _ctx(a, _start(2) + 30))
        assert [c["report_day"] for c in turn["closing"]] == [str(_day(1))]
        assert turn["days_not_reconciled"] == 0
        assert [r["finding_id"] for r in await _named(conn, a)] == [
            rows[0]["finding_id"]]
        assert DAY0 not in await _days(conn, a)
    finally:
        await conn.close()


class _Hooked:
    """An asyncpg connection whose statements can be held or observed: the
    INSERT of an AUDREY_DAY_NOT_RECONCILED finding, or of a report version
    (held), and the read of the findings already recorded for the days
    looked at (observed)."""

    def __init__(self, conn, *, before_insert=None, after_read=None,
                 before_report_insert=None):
        self._c = conn
        self._before_insert = before_insert
        self._after_read = after_read
        self._before_report_insert = before_report_insert

    def __getattr__(self, name):
        return getattr(self._c, name)

    async def _run(self, meth, sql, *args, **kw):
        s = " ".join(str(sql).split())
        if self._before_insert and s.startswith(
                "INSERT INTO paper_audrey_findings") and KIND in args:
            await self._before_insert()
        if self._before_report_insert and s.startswith(
                "INSERT INTO paper_audrey_reports"):
            await self._before_report_insert()
        got = await getattr(self._c, meth)(sql, *args, **kw)
        if self._after_read and s.startswith(
                "SELECT finding_id FROM paper_audrey_findings"):
            self._after_read()
        return got

    async def execute(self, sql, *args, **kw):
        return await self._run("execute", sql, *args, **kw)

    async def fetchval(self, sql, *args, **kw):
        return await self._run("fetchval", sql, *args, **kw)

    async def fetchrow(self, sql, *args, **kw):
        return await self._run("fetchrow", sql, *args, **kw)

    async def fetch(self, sql, *args, **kw):
        return await self._run("fetch", sql, *args, **kw)


async def _first_of(*events: asyncio.Event, timeout: float) -> None:
    waits = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        await asyncio.wait(waits, timeout=timeout,
                           return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waits:
            w.cancel()


@pg
async def test_two_concurrent_callers_and_repeated_passes_store_one_finding():
    """The first caller is held at the finding's INSERT until the second has
    read the recorded findings (so both saw the day unrecorded) or 3 s pass;
    the second starts once the first reaches that INSERT (or finishes)."""
    conn = await H.connect()
    c1 = await H.connect()
    c2 = await H.connect()
    try:
        a = await H.new_account(conn, "audpair", now=T)
        await PA.step(conn, _ctx(a, T))
        await _fill(conn, a, "d1", _start(1) + 12 * 3600)
        resume = _start(2) + 10 * 3600
        first_at_insert, first_done = asyncio.Event(), asyncio.Event()
        second_read = asyncio.Event()
        interleaved = []

        async def hold():
            first_at_insert.set()
            try:
                await asyncio.wait_for(second_read.wait(), 3.0)
            except TimeoutError:
                pass
            interleaved.append(second_read.is_set())

        async def first():
            try:
                return await PA.step(_Hooked(c1, before_insert=hold),
                                     _ctx(a, resume))
            finally:
                first_done.set()

        async def second():
            await _first_of(first_at_insert, first_done, timeout=10.0)
            return await PA.step(_Hooked(c2, after_read=second_read.set),
                                 _ctx(a, resume + 1))

        r1, r2 = await asyncio.gather(first(), second())
        rows = await _named(conn, a)
        assert [r["subject"] for r in rows] == [str(_day(1))]
        # both callers saw the day unrecorded before either stored it
        assert interleaved == [True]
        # one stored it; the other, serialized behind the day's lock, did not
        assert sorted([r1.get("days_not_reconciled"),
                       r2.get("days_not_reconciled")]) == [0, 1]
        assert await _tasks(conn, rows[0]["improvement_task_id"]) == 1
        # repeated passes (and the next day turn) on either connection
        for c, at in ((c1, resume + 120), (c2, resume + 180),
                      (conn, _start(3) + 30)):
            assert (await PA.step(c, _ctx(a, at)))[
                "days_not_reconciled"] == 0
        assert [r["finding_id"] for r in await _named(conn, a)] == [
            rows[0]["finding_id"]]
        assert _day(1) not in await _days(conn, a)
    finally:
        for c in (c2, c1, conn):
            await c.close()


@pg
async def test_a_report_landing_while_the_day_is_looked_at_is_not_named():
    """A late pass for the day (a host whose clock is still in it) stores the
    day's first version while a pass in the next day is looking at it. The
    writer holds the day's report lock and is held at its INSERT until the
    looking pass has read the day as unrecorded and with no version (or 3 s
    pass). The looking pass then waits on that lock, reads the day again and
    names nothing: the day has a report."""
    conn = await H.connect()
    c1 = await H.connect()
    c2 = await H.connect()
    try:
        a = await H.new_account(conn, "audlate", now=T)
        sess = _ctx(a, T)["session"]
        await PA.step(conn, _ctx(a, T))
        await _fill(conn, a, "d1", _start(1) + 12 * 3600)
        writer_at_insert, writer_done = asyncio.Event(), asyncio.Event()
        looked = asyncio.Event()
        interleaved = []

        async def hold():
            writer_at_insert.set()
            try:
                await asyncio.wait_for(looked.wait(), 3.0)
            except TimeoutError:
                pass
            interleaved.append(looked.is_set())

        async def late_writer():
            try:
                return await PA.write_report(
                    _Hooked(c1, before_report_insert=hold), session=sess,
                    account_id=a["account_id"], now=_start(2) - 0.5)
            finally:
                writer_done.set()

        async def looking_pass():
            await _first_of(writer_at_insert, writer_done, timeout=10.0)
            return await PA.step(_Hooked(c2, after_read=looked.set),
                                 _ctx(a, _start(2) + 30))

        w, got = await asyncio.gather(late_writer(), looking_pass())
        # the looking pass read the day while the late version was held
        assert interleaved == [True]
        assert w["written"] is True and w["report_day"] == str(_day(1))
        assert await _named(conn, a) == []
        assert got["days_not_reconciled"] == 0
        assert (await _days(conn, a))[_day(1)] == [(1, False)]
        # the next pass closes the late-reported day; still nothing named
        nxt = await PA.step(conn, _ctx(a, _start(2) + 90))
        assert [c["report_day"] for c in nxt["closing"]] == [str(_day(1))]
        assert (await _days(conn, a))[_day(1)] == [(1, False), (2, True)]
        assert await _named(conn, a) == []
    finally:
        for c in (c2, c1, conn):
            await c.close()


@pg
async def test_the_first_pass_after_an_old_worker_turned_midnight_closes():
    """The code before day closing never stores a final version: at the day
    turn it writes the old day's last version and the new day's first. When
    such a worker reached midnight first, the new code's first pass (in the
    deploy day) closes the day before the deploy; the day before that stays
    history, and the earlier day with fills and no report is not named."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "auddeploy", now=_start(-2) + 3600)
        sess = _ctx(a, T)["session"]
        acct = a["account_id"]
        # day -1: fills, no report (history before the first closed day)
        await _fill(conn, a, "hist", _start(-1) + 12 * 3600)

        async def old(now):
            """A version as the code before day closing writes it."""
            return await PA.write_report(conn, session=sess, account_id=acct,
                                         now=now)
        assert (await old(T))["written"]                      # day 0 v1
        assert (await old(END0 + 3600))["written"]            # day 1 v1
        await _reserve(conn, a, "o1", END0 + 7200)
        assert (await old(END0 + 7300))["written"]            # day 1 v2
        # the old worker reaches midnight first: day 1's last version (a
        # new one only if its content changed) and day 2's first
        await old(_start(2) - 0.001)
        assert (await old(_start(2) + 5))["written"]          # day 2 v1
        before = await _days(conn, a)
        n1 = len(before[_day(1)])
        assert set(before) == {DAY0, _day(1), _day(2)} and n1 >= 2
        assert before[DAY0] == [(1, False)] and before[_day(2)] == [(1, False)]
        assert not any(f for _v, f in before[_day(1)])
        # a pass whose clock is behind the record (a host still in day 0)
        # writes, closes and names nothing -- not even day -1
        back = await PA.step(conn, _ctx(a, T))
        assert back["report"] is None and back["closing"] == []
        assert await _named(conn, a) == [] and await _days(conn, a) == before
        # THE NEW CODE'S FIRST PASS, in the deploy day
        got = await PA.step(conn, _ctx(a, _start(2) + 3600))
        days = await _days(conn, a)
        assert days[_day(1)] == before[_day(1)] + [(n1 + 1, True)]
        assert days[DAY0] == [(1, False)]              # history, not closed
        assert got["closing"] == [{"report_day": str(_day(1)),
                                   "written": True, "version": n1 + 1,
                                   "why": None, "final": True}]
        assert got["days_closed"] == 1 and got["report_held"] is None
        closing = await conn.fetchrow(
            "SELECT generated_at, report FROM paper_audrey_reports "
            " WHERE session_id=$1 AND report_day=$2 AND version=$3",
            a["session_id"], _day(1), n1 + 1)
        assert L._epoch(closing["generated_at"]) == pytest.approx(
            _start(2) - 0.001, abs=1e-3)
        assert H.j(closing["report"])["window"]["start"] == _start(1)
        # no backfill: the earlier day with fills and no report is history
        assert got["days_not_reconciled"] == 0
        assert await _named(conn, a) == []
        # the deploy day closes at its own turn; nothing else changes
        turn = await PA.step(conn, _ctx(a, _start(3) + 30))
        assert [c["report_day"] for c in turn["closing"]] == [str(_day(2))]
        assert await _finals_per_day(conn, a) == {
            DAY0: 0, _day(1): 1, _day(2): 1, _day(3): 0}
        assert await _named(conn, a) == []
    finally:
        await conn.close()
