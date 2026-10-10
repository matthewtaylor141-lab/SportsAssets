"""rc6.2: Audrey closes the day she last reported, after any gap, exactly once.

The day-turn write closed the calendar day before the report clock's day,
not the day last reported. After a whole New York day with no pass (worker
outage, paper control off, the audrey step failing or the pass timing out
all day) the last reported day never got a final version, and the empty day
before the resume day got a final report it never had. A closing write and
an intra-day write were also two separate statements: a no-op INSERT ... ON
CONFLICT DO NOTHING still returned written = final = true (a final row could
be lost), and a day whose latest version was final could take a later
non-final version, then a second final.

Proven against Postgres through the real step() and write_report():
  * after a gap of one or two whole days the last reported day gets exactly
    one final version (its window the whole day), the gap days get no report
    at all, and later passes never touch either again;
  * every day after the newest closed day that has versions and no final is
    closed; a session with no closed day closes only its newest reported day
    (no retroactive closing of history);
  * the closing boundary is the day's end, inclusive, and a closing write
    for a day with no version is refused;
  * nothing is appended to a closed day, a final in ANY version closes the
    day, and a report clock behind the newest report writes nothing;
  * two writers of one day are serialized: one final row, and the loser says
    so (written = false), whichever write lands first;
  * a closing INSERT that stores nothing (its version taken by a writer
    outside the lock) is not reported written, holds the pass (no later day
    closed past it, no new report), and the next pass closes it.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import paper_audrey as PA

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

NY = "America/New_York"
T = H.T0                                   # 2026-09-24 21:33:20 New York
DAY0, START0, END0 = PA.day_bounds(T)
DAY = 86400.0


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


async def _row(conn, a, day, version):
    return await conn.fetchrow(
        "SELECT * FROM paper_audrey_reports WHERE session_id=$1 "
        "   AND report_day=$2 AND version=$3", a["session_id"], day, version)


async def _finals_per_day(conn, a) -> dict:
    return {r["report_day"]: int(r["n"]) for r in await conn.fetch(
        "SELECT report_day, count(*) FILTER (WHERE final) AS n "
        "  FROM paper_audrey_reports WHERE session_id=$1 GROUP BY 1",
        a["session_id"])}


async def _two_versions_on_day0(conn, a):
    assert (await PA.step(conn, _ctx(a, T)))["report"]["version"] == 1
    await _reserve(conn, a, "o1", T + 60)
    r2 = await PA.step(conn, _ctx(a, T + 60 + PA.REPORT_EVERY_S))
    assert r2["report"]["written"] and r2["report"]["version"] == 2
    assert await _days(conn, a) == {DAY0: [(1, False), (2, False)]}


def test_day_window_is_the_local_day_across_dst():
    for d, hours in ((dt.date(2026, 3, 8), 23), (dt.date(2026, 11, 1), 25),
                     (DAY0, 24)):
        day, start, end = PA.day_window(d, NY)
        assert day == d and end - start == hours * 3600
        assert PA.day_bounds(start, NY) == (d, start, end)
        assert PA.day_bounds(end - 0.001, NY) == (d, start, end)
        assert PA.day_bounds(end, NY)[0] == d + dt.timedelta(days=1)
    assert PA.day_window(DAY0, NY) == (DAY0, START0, END0)
    for k in range(6):
        assert PA.day_window(_day(k), NY)[1:] == (_start(k), _start(k + 1))


@pg
@pytest.mark.parametrize("gap_days,resume_after_midnight_s", [
    (1, 10 * 3600),           # the reviewers' probe: resume 09-26 10:00
    (1, 30),                  # resume 09-26 00:00:30
    (2, 30)])                 # two whole days with no pass
async def test_a_gap_of_whole_days_closes_the_last_reported_day_only(
        gap_days, resume_after_midnight_s):
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audgap", now=T)
        await _two_versions_on_day0(conn, a)
        resume_day = _day(gap_days + 1)
        resume = _start(gap_days + 1) + resume_after_midnight_s
        got = await PA.step(conn, _ctx(a, resume))
        days = await _days(conn, a)
        # the last reported day gets its one final version, the gap days get
        # no report at all, and the resume day is open
        assert days == {DAY0: [(1, False), (2, False), (3, True)],
                        resume_day: [(1, False)]}
        assert got["report"]["report_day"] == str(resume_day)
        assert got["report"]["written"] and got["report"]["version"] == 1
        assert got["closing"] == [{"report_day": str(DAY0), "written": True,
                                   "version": 3, "why": None,
                                   "final": True}]
        assert got["days_closed"] == 1 and got["report_held"] is None
        closing = await _row(conn, a, DAY0, 3)
        assert L._epoch(closing["generated_at"]) == pytest.approx(
            END0 - 0.001, abs=1e-3)
        rep = H.j(closing["report"])
        assert rep["report_day"] == str(DAY0)
        assert rep["window"]["start"] == START0
        assert rep["window"]["end"] == END0
        assert rep["window"]["through"] == pytest.approx(END0 - 0.001)
        # final is the day closed; reconciles is the report's own
        assert closing["reconciles"] is rep["reconciliation"]["reconciles"]
        # later passes on the resume day never touch day 0 or the gap
        await _reserve(conn, a, "o2", resume + 60)
        later = await PA.step(conn, _ctx(a, resume + 60
                                         + PA.REPORT_EVERY_S))
        assert later["report"]["written"] and later["report"]["version"] == 2
        # the resume day is closed at its own turn, day 0 is not re-closed
        nxt = _start(gap_days + 2) + 45
        turn = await PA.step(conn, _ctx(a, nxt))
        days = await _days(conn, a)
        assert days[DAY0] == [(1, False), (2, False), (3, True)]
        assert days[resume_day] == [(1, False), (2, False), (3, True)]
        assert set(days) == {DAY0, resume_day, _day(gap_days + 2)}
        for k in range(1, gap_days + 1):
            assert _day(k) not in days
        assert (await _finals_per_day(conn, a)) == {
            DAY0: 1, resume_day: 1, _day(gap_days + 2): 0}
        assert later["closing"] == [] and later["days_closed"] == 0
        assert [c["report_day"] for c in turn["closing"]] == [str(resume_day)]
    finally:
        await conn.close()


@pg
async def test_every_reported_day_after_the_last_closed_day_is_closed():
    """Days 1 and 2 carry versions from a writer that never closes (the code
    before this fix, during a rolling deploy); day 3 has none."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audevery", now=T)
        sess = _ctx(a, T)["session"]
        await _two_versions_on_day0(conn, a)
        await PA.step(conn, _ctx(a, END0 + 30))      # day 0 closed
        assert await _days(conn, a) == {
            DAY0: [(1, False), (2, False), (3, True)], _day(1): [(1, False)]}
        w = await PA.write_report(conn, session=sess,
                                  account_id=a["account_id"],
                                  now=END0 + DAY + 3600)
        assert w["written"] and w["report_day"] == str(_day(2))
        d4 = _start(4) + 3600
        got = await PA.step(conn, _ctx(a, d4))
        days = await _days(conn, a)
        assert days == {DAY0: [(1, False), (2, False), (3, True)],
                        _day(1): [(1, False), (2, True)],
                        _day(2): [(1, False), (2, True)],
                        _day(4): [(1, False)]}
        again = await PA.step(conn, _ctx(a, d4 + PA.REPORT_EVERY_S))
        assert (await _days(conn, a))[_day(2)] == [(1, False), (2, True)]
        assert [(c["report_day"], c["written"], c["final"])
                for c in got["closing"]] == [(str(_day(1)), True, True),
                                             (str(_day(2)), True, True)]
        assert again["closing"] == []
    finally:
        await conn.close()


@pg
async def test_a_session_with_no_closed_day_closes_only_its_newest_day():
    """No retroactive closing: the days reported before any day was closed
    stay as they are; the newest reported day is closed once it is over."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audhist", now=T)
        sess = _ctx(a, T)["session"]
        for at in (T, END0 + 3600):
            w = await PA.write_report(conn, session=sess,
                                      account_id=a["account_id"], now=at)
            assert w["written"]
        assert await _days(conn, a) == {DAY0: [(1, False)],
                                        _day(1): [(1, False)]}
        d3 = _start(3) + 10 * 3600                 # 2026-09-27 10:00
        got = await PA.step(conn, _ctx(a, d3))
        assert await _days(conn, a) == {DAY0: [(1, False)],
                                        _day(1): [(1, False), (2, True)],
                                        _day(3): [(1, False)]}
        assert [c["report_day"] for c in got["closing"]] == [str(_day(1))]
    finally:
        await conn.close()


@pg
async def test_the_closing_boundary_is_the_end_of_the_day_inclusive():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audedge", now=T)
        sess = _ctx(a, T)["session"]
        await PA.step(conn, _ctx(a, T))
        inside = await PA.write_report(conn, session=sess,
                                       account_id=a["account_id"],
                                       now=END0 - 0.001,
                                       closed_at=END0 - 1e-6)
        assert inside["final"] is False
        assert not any(f for _v, f in (await _days(conn, a))[DAY0])
        at_end = await PA.write_report(conn, session=sess,
                                       account_id=a["account_id"],
                                       now=END0 - 0.001, closed_at=END0)
        assert at_end["written"] is True and at_end["final"] is True
        rows = (await _days(conn, a))[DAY0]
        assert rows[-1] == (at_end["version"], True)
        assert await _finals_per_day(conn, a) == {DAY0: 1}
        # a closing write for a day with no version is refused: a day no
        # pass reported gets no report
        none = await PA.write_report(conn, session=sess,
                                     account_id=a["account_id"],
                                     now=END0 + DAY - 0.001,
                                     closed_at=END0 + 2 * DAY)
        assert none["written"] is False
        assert none["why"] == "NO_REPORT_TO_CLOSE" and none["final"] is False
        assert _day(1) not in await _days(conn, a)
    finally:
        await conn.close()


@pg
async def test_nothing_is_appended_to_a_closed_day():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audshut", now=T)
        sess = _ctx(a, T)["session"]
        await _two_versions_on_day0(conn, a)
        await PA.step(conn, _ctx(a, END0 + 0.5))
        before = await _days(conn, a)
        assert before[DAY0] == [(1, False), (2, False), (3, True)]
        # changed content, written for an instant inside the closed day
        await _reserve(conn, a, "late", END0 - 0.05)
        w = await PA.write_report(conn, session=sess,
                                  account_id=a["account_id"],
                                  now=END0 - 0.04)
        assert w["written"] is False and w["why"] == "ALREADY_FINAL"
        assert w["final"] is True and w["version"] == 3
        c = await PA.write_report(conn, session=sess,
                                  account_id=a["account_id"],
                                  now=END0 - 0.001, closed_at=END0 + 90)
        assert c["written"] is False and c["why"] == "ALREADY_FINAL"
        assert (await _days(conn, a))[DAY0] == before[DAY0]
    finally:
        await conn.close()


@pg
async def test_a_final_in_any_version_closes_the_day():
    """A day whose final version is not its latest (rows a writer without
    this rule could leave): never closed a second time, never appended to,
    and never picked as a day to close."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audany", now=T)
        sess = _ctx(a, T)["session"]
        for ver, final in ((1, True), (2, False)):
            await conn.execute(
                "INSERT INTO paper_audrey_reports (report_id, session_id, "
                " account_id, report_day, reporting_tz, version, "
                " generated_at, final, reconciles, report, digest) VALUES "
                " ($1,$2,$3,$4,$5,$6,to_timestamp($7),$8,true,'{}'::jsonb,$9)",
                "paperrep:test:%s:%s" % (a["session_id"], ver),
                a["session_id"], a["account_id"], DAY0, NY, ver,
                END0 - 10 + ver, final, "digest-%s" % ver)
        c = await PA.write_report(conn, session=sess,
                                  account_id=a["account_id"],
                                  now=END0 - 0.001, closed_at=END0 + 60)
        assert c["written"] is False and c["why"] == "ALREADY_FINAL"
        assert c["version"] == 1
        await _reserve(conn, a, "o1", END0 - 1)
        w = await PA.write_report(conn, session=sess,
                                  account_id=a["account_id"],
                                  now=END0 - 0.5)
        assert w["written"] is False and w["why"] == "ALREADY_FINAL"
        assert await PA.days_to_close(conn, session=sess,
                                      closed_at=END0 + DAY) == []
        assert await _days(conn, a) == {DAY0: [(1, True), (2, False)]}
        assert await _finals_per_day(conn, a) == {DAY0: 1}
    finally:
        await conn.close()


@pg
async def test_a_report_clock_behind_the_newest_report_writes_nothing():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audback", now=T)
        await _two_versions_on_day0(conn, a)
        turn = await PA.step(conn, _ctx(a, END0 + 0.5))
        assert turn["report"]["report_day"] == str(_day(1))
        before = await _days(conn, a)
        assert before == {DAY0: [(1, False), (2, False), (3, True)],
                          _day(1): [(1, False)]}
        # a pass whose clock is behind midnight (stepped back, or a host
        # behind the one that turned the day), with changed content
        await _reserve(conn, a, "late", END0 - 0.05)
        back = await PA.step(conn, _ctx(a, END0 - 0.04))
        assert await _days(conn, a) == before          # no 09-23, no v4
        # a day and a half behind: still nothing, not even a first version
        # of a day no pass reported (2026-09-23)
        far = await PA.step(conn, _ctx(a, START0 - DAY / 2))
        assert await _days(conn, a) == before
        assert back["report"] is None and back["closing"] == []
        assert back["report_held"] == "REPORT_CLOCK_BEHIND_THE_NEWEST_REPORT"
        assert far["report"] is None and far["closing"] == []
    finally:
        await conn.close()


class _Hooked:
    """An asyncpg connection whose statements on paper_audrey_reports can be
    held or observed, to force the interleave of two writers of one day."""

    def __init__(self, conn, *, before_insert=None, after_read=None):
        self._c = conn
        self._before_insert = before_insert
        self._after_read = after_read

    def __getattr__(self, name):
        return getattr(self._c, name)

    async def _run(self, meth, sql, *args, **kw):
        s = " ".join(str(sql).split())
        if self._before_insert and s.startswith(
                "INSERT INTO paper_audrey_reports"):
            await self._before_insert()
        got = await getattr(self._c, meth)(sql, *args, **kw)
        if self._after_read and s.startswith("SELECT") and \
                "FROM paper_audrey_reports WHERE session_id=$1 AND " \
                "report_day=$2" in s:
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


@pg
@pytest.mark.parametrize("second", ["closing", "intraday"])
async def test_two_writers_of_one_day_store_one_final_and_the_loser_says_so(
        second):
    """The first writer (a closing write) is held at its INSERT until the
    second writer has read the day's versions (or 3 s pass: a second writer
    blocked on the day's lock never reads). Without the lock both read the
    same latest version, and one INSERT stores nothing while still being
    reported written (the final row lost when the intra-day one lands
    first)."""
    conn = await H.connect()
    c1 = await H.connect()
    c2 = await H.connect()
    try:
        a = await H.new_account(conn, "audrace", now=T)
        sess = _ctx(a, T)["session"]
        await PA.step(conn, _ctx(a, T))
        if second == "intraday":
            await _reserve(conn, a, "o1", END0 - 1)
        first_at_insert, second_read = asyncio.Event(), asyncio.Event()
        interleaved = []

        async def hold():
            first_at_insert.set()
            try:
                await asyncio.wait_for(second_read.wait(), 3.0)
            except TimeoutError:
                pass
            # did the second writer read the day's versions between the
            # first writer's read and its insert?
            interleaved.append(second_read.is_set())

        h1 = _Hooked(c1, before_insert=hold)
        h2 = _Hooked(c2, after_read=second_read.set)

        async def first():
            return await PA.write_report(h1, session=sess,
                                         account_id=a["account_id"],
                                         now=END0 - 0.001,
                                         closed_at=END0 + 30)

        async def second_writer():
            await asyncio.wait_for(first_at_insert.wait(), 10.0)
            if second == "closing":
                return await PA.write_report(h2, session=sess,
                                             account_id=a["account_id"],
                                             now=END0 - 0.001,
                                             closed_at=END0 + 31)
            return await PA.write_report(h2, session=sess,
                                         account_id=a["account_id"],
                                         now=END0 - 0.4)

        r1, r2 = await asyncio.gather(first(), second_writer())
        rows = (await _days(conn, a))[DAY0]
        # serialized: the second writer could not read the day while the
        # first held its insert
        assert interleaved == [False]
        # exactly one final row, and it is the day's last version
        assert [f for _v, f in rows].count(True) == 1
        assert rows[-1][1] is True
        # every write that says it stored a row did: the stored versions are
        # version 1 plus exactly the writes reporting written = true
        written = [r for r in (r1, r2) if r["written"]]
        assert len(rows) == 1 + len(written)
        assert sorted(r["version"] for r in written) == \
            [v for v, _f in rows[1:]]
        # the closing write held at its INSERT stored the final version; the
        # second writer, serialized behind it, says the day is closed
        assert r1["written"] is True and r1["final"] is True
        assert r2["written"] is False and r2["why"] == "ALREADY_FINAL"
        assert r2["final"] is True and r2["version"] == r1["version"]
    finally:
        for c in (c2, c1, conn):
            await c.close()


async def _inject(conn, a, day, version):
    """A version stored by a writer that does not take the day's lock (the
    code before this fix, during a rolling deploy)."""
    await conn.execute(
        "INSERT INTO paper_audrey_reports (report_id, session_id, account_id,"
        " report_day, reporting_tz, version, generated_at, final, reconciles,"
        " report, digest) VALUES ($1,$2,$3,$4,$5,$6,now(),false,true,"
        " '{}'::jsonb,'injected')",
        "paperrep:injected:%s:%s:%s" % (a["session_id"], day, version),
        a["session_id"], a["account_id"], day, NY, version)


@pg
async def test_a_closing_insert_that_stores_nothing_holds_the_pass():
    """The closing INSERT finds its version taken: it is not reported
    written or final, no later day is closed past it, the pass writes no
    new report (so the next pass still sees the day open), and the next
    pass closes it and the day after it."""
    conn = await H.connect()
    c1 = await H.connect()
    try:
        a = await H.new_account(conn, "audtaken", now=T)
        sess = _ctx(a, T)["session"]
        await _two_versions_on_day0(conn, a)
        await PA.step(conn, _ctx(a, END0 + 30))      # day 0 closed, day 1 v1
        await PA.write_report(conn, session=sess, account_id=a["account_id"],
                              now=_start(2) + 3600)  # day 2 v1, never closed
        injected = []

        async def take_the_version():
            if not injected:
                injected.append(True)
                await _inject(conn, a, _day(1), 2)

        d4 = _start(4) + 3600
        got = await PA.step(_Hooked(c1, before_insert=take_the_version),
                            _ctx(a, d4))
        assert injected
        assert await _days(conn, a) == {
            DAY0: [(1, False), (2, False), (3, True)],
            _day(1): [(1, False), (2, False)], _day(2): [(1, False)]}
        assert got["closing"] == [{"report_day": str(_day(1)),
                                   "written": False, "version": 2,
                                   "why": "VERSION_TAKEN", "final": False}]
        assert got["days_closed"] == 0 and got["report"] is None
        assert got["report_held"] == "CLOSING_VERSION_NOT_STORED"
        nxt = await PA.step(conn, _ctx(a, d4 + 60))
        assert await _days(conn, a) == {
            DAY0: [(1, False), (2, False), (3, True)],
            _day(1): [(1, False), (2, False), (3, True)],
            _day(2): [(1, False), (2, True)], _day(4): [(1, False)]}
        assert [(c["report_day"], c["written"]) for c in nxt["closing"]] == \
            [(str(_day(1)), True), (str(_day(2)), True)]
        assert nxt["days_closed"] == 2 and nxt["report_held"] is None
    finally:
        await c1.close()
        await conn.close()
