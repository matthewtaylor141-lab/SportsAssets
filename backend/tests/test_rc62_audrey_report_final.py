"""(RC6.2) AUDREY'S DAILY REPORT: THE CLOSING VERSION OF A DAY IS FINAL.

Production (audit A4): 811 paper_audrey_reports, 0 final. write_report set
final = (now >= end of the day), but step() writes the previous day's
closing version at now = start of the new day - 1 ms, an instant INSIDE the
day it covers, so the closing version could never be final. The closing
write now marks it final explicitly -- also when its content equals the
day's newest non-final version (it still writes one final version) -- and
an in-day write stays non-final.

(rc6.3b merge) This file came with be-truth's 56aa82a1, an earlier fix of
the same defect that the candidate supersedes with rc6/audrey-final
(98fdc79d) and rc6/audrey-gap-record (9a597c78). Under those, the closing
write is final because the caller passes the instant it observed after the
day (write_report(..., closed_at=...) with closed_at at or after the day's
end), not by an explicit final flag, and a day with a final version takes
nothing more: a repeat closing write is ALREADY_FINAL, not NO_CHANGE. The
second test is adapted to that API and that refusal; every property it
pins that the candidate also guarantees is still asserted (an identical
non-final last version still gets exactly one final version, version + 1;
the day holds [non-final, final]). The first test is unchanged.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_session as S
from sportsassets.agents import paper_audrey as PA
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
TZ = "America/New_York"


async def _session(conn, acct):
    sess = await S.active_session(conn, acct["account_id"])
    assert sess and sess["session_id"] == acct["session_id"], sess
    return sess


async def _reports(conn, acct):
    return [dict(r) for r in await conn.fetch(
        "SELECT report_day, version, final FROM paper_audrey_reports "
        " WHERE session_id=$1 ORDER BY report_day, version",
        acct["session_id"])]


@pg
async def test_the_previous_days_last_version_is_final_at_the_turn():
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        # noon (New York) of a fixed day, then 12 h later: the day turned
        day0, start0, end0 = PA.day_bounds(1_790_000_000.0, TZ)
        noon = start0 + 12 * 3600
        acct = await H.new_account(conn, "audfinal", now=noon - 60)
        sess = await _session(conn, acct)
        ctx = {"session": sess, "session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": noon}
        await PA.step(conn, ctx)
        got = await _reports(conn, acct)
        assert got and all(r["report_day"] == day0 for r in got)
        assert not any(r["final"] for r in got), got

        ctx["now"] = end0 + 60
        await PA.step(conn, ctx)
        got = await _reports(conn, acct)
        prior = [r for r in got if r["report_day"] == day0]
        today = [r for r in got if r["report_day"] != day0]
        # the prior day's LAST version is final; earlier ones are not
        assert prior[-1]["final"] is True, got
        assert not any(r["final"] for r in prior[:-1]), got
        # the new day's in-day version is not final
        assert today and not any(r["final"] for r in today), got
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_final_write_after_an_identical_non_final_version_writes_once():
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        day0, start0, end0 = PA.day_bounds(1_790_000_000.0, TZ)
        acct = await H.new_account(conn, "audfinal2", now=start0 + 60)
        sess = await _session(conn, acct)
        a = await PA.write_report(conn, session=sess,
                                  account_id=acct["account_id"],
                                  now=start0 + 3600)
        assert a["written"] and a["final"] is False
        # (rc6.3b) the closing write: the instant observed after the day is
        # passed as closed_at (the day's end, inclusive), the candidate's
        # API, in place of 56aa82a1's explicit final=True
        b = await PA.write_report(conn, session=sess,
                                  account_id=acct["account_id"],
                                  now=start0 + 3600, closed_at=end0)
        assert b["written"] and b["final"] is True
        assert b["version"] == a["version"] + 1
        c = await PA.write_report(conn, session=sess,
                                  account_id=acct["account_id"],
                                  now=start0 + 3600, closed_at=end0)
        # (rc6.3b) nothing is appended to a day after its final version:
        # the repeat closing write is refused ALREADY_FINAL (56aa82a1 said
        # NO_CHANGE) and names the day's one final version
        assert c["written"] is False and c["why"] == "ALREADY_FINAL"
        assert c["final"] is True and c["version"] == b["version"]
        got = await _reports(conn, acct)
        assert [r["final"] for r in got] == [False, True]
    finally:
        await tx.rollback()
        await conn.close()
