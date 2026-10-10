"""rc6.2: Audrey's closing version of a reporting day is recorded final.

Production: 811 paper_audrey_reports rows, none final. step() writes the
previous day's closing version when the day turns with `now` one millisecond
before midnight, and write_report() stored final = (now >= the end of the
day of `now`) -- the end of that same day, so never true. The closing
version is now judged against the day it covers (the report clock, in the
new day, is at or after that day's end), and the append-only table gets a
final version even when the content did not change since the day's last
version.

Proven against Postgres through the real step():
  * when the day turns, the previous day's last version is final (content
    changed before midnight, or unchanged and recorded again as final),
    exactly once, and later passes never close it again;
  * intra-day versions stay final = false, up to the last instant of the day;
  * a day that does not reconcile is final with reconciles = false: final
    says the day is closed, never that it reconciles.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import paper_audrey as PA

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

T = H.T0                                   # 2026-09-24 21:33:20 New York
DAY0, START0, END0 = PA.day_bounds(T)
DAY1 = PA.day_bounds(END0)[0]


def _ctx(a, now):
    return {"account_id": a["account_id"], "session_id": a["session_id"],
            "config": a["config"], "now": now, "clock": lambda: now,
            "session": {"session_id": a["session_id"], "config": a["config"],
                        "reporting_tz": "America/New_York"},
            "fee_fn": H.zero_fee, "deadline": 1e18}


async def _reserve(conn, a, key, at):
    """A resting order: its reservation changes the report's balances."""
    o = H.order(a, key=key, qty=10, limit=0.40, at=at, ttl=7 * 86400,
                slug="%s:mkt" % a["account_id"], group_id="paper_g_%s_%s" % (
                    a["account_id"][-10:], key))
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got.get("order"), got


async def _versions(conn, a, day):
    return await conn.fetch(
        "SELECT version, final, reconciles, digest, generated_at, report "
        "  FROM paper_audrey_reports WHERE session_id=$1 AND report_day=$2 "
        " ORDER BY version", a["session_id"], day)


def test_the_day_turn_falls_on_midnight_new_york():
    assert str(DAY0) == "2026-09-24" and str(DAY1) == "2026-09-25"
    assert START0 < T < END0 and END0 - T < PA.REPORT_EVERY_S * 10


@pg
@pytest.mark.parametrize("late_activity", [False, True])
async def test_the_day_turn_records_the_previous_days_closing_version_final(
        late_activity):
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audfin", now=T)
        sess = _ctx(a, T)["session"]
        assert (await PA.step(conn, _ctx(a, T)))["report"]["version"] == 1
        await _reserve(conn, a, "o1", T + 60)
        r2 = await PA.step(conn, _ctx(a, T + 60 + PA.REPORT_EVERY_S))
        assert r2["report"]["written"] and r2["report"]["version"] == 2
        await _reserve(conn, a, "o2", T + 120 + PA.REPORT_EVERY_S)
        # the last intra-day pass, at the day's last millisecond: its window
        # is the closing version's own ([start, end) of the day), so both
        # read the same rows and "unchanged" does not depend on what other
        # tests left in the shared database (the book-read counts are not
        # per account)
        r3 = await PA.step(conn, _ctx(a, END0 - 0.001))
        assert r3["report"]["written"] and r3["report"]["version"] == 3
        if late_activity:
            await _reserve(conn, a, "o3", END0 - 0.0005)
        # THE DAY TURNS
        turn = END0 + 30
        r4 = await PA.step(conn, _ctx(a, turn))
        assert r4["report"]["report_day"] == str(DAY1)
        assert r4["report"]["written"] and r4["report"]["version"] == 1
        rows = await _versions(conn, a, DAY0)
        assert [r["version"] for r in rows] == [1, 2, 3, 4]
        assert [r["final"] for r in rows] == [False, False, False, True]
        closing = rows[-1]
        assert L._epoch(closing["generated_at"]) == pytest.approx(
            END0 - 0.001, abs=1e-3)
        assert closing["reconciles"] is True
        assert H.j(closing["report"])["window"] == \
            H.j(rows[2]["report"])["window"]
        # unchanged content is recorded again as the final version
        assert (closing["digest"] == rows[2]["digest"]) is (not late_activity)
        if not late_activity:
            assert H.j(closing["report"]) == H.j(rows[2]["report"])
        assert [r["final"] for r in await _versions(conn, a, DAY1)] == [False]
        # later passes in the new day never close (or re-close) day 0
        await _reserve(conn, a, "o4", turn + 60)
        r5 = await PA.step(conn, _ctx(a, turn + 60 + PA.REPORT_EVERY_S))
        assert r5["report"]["written"] and r5["report"]["report_day"] == \
            str(DAY1)
        assert [r["final"] for r in await _versions(conn, a, DAY0)] == \
            [False, False, False, True]
        assert [r["final"] for r in await _versions(conn, a, DAY1)] == \
            [False, False]
        # a repeated closing write (a restart between step's two writes)
        # does not close the day twice
        again = await PA.write_report(conn, session=sess,
                                      account_id=a["account_id"],
                                      now=END0 - 0.001, closed_at=turn + 120)
        assert again["written"] is False and again["why"] == "ALREADY_FINAL"
        assert len(await _versions(conn, a, DAY0)) == 4
        agg = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE final) AS finals, count(*) AS n "
            "  FROM paper_audrey_reports WHERE session_id=$1",
            a["session_id"])
        assert (agg["finals"], agg["n"]) == (1, 6)
    finally:
        await conn.close()


@pg
async def test_intraday_versions_stay_not_final_to_the_last_instant():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audopen", now=T)
        sess = _ctx(a, T)["session"]
        await PA.step(conn, _ctx(a, T))
        await _reserve(conn, a, "o1", T + 60)
        await PA.step(conn, _ctx(a, T + 60 + PA.REPORT_EVERY_S))
        await _reserve(conn, a, "o2", END0 - 1.0)
        await PA.step(conn, _ctx(a, END0 - 0.5))
        # written directly at the day's last millisecond: still open
        await _reserve(conn, a, "o3", END0 - 0.1)
        w = await PA.write_report(conn, session=sess,
                                  account_id=a["account_id"],
                                  now=END0 - 0.001)
        assert w["written"] and w["final"] is False
        # a closing instant inside the covered day does not close it
        await _reserve(conn, a, "o4", END0 - 0.05)
        w = await PA.write_report(conn, session=sess,
                                  account_id=a["account_id"],
                                  now=END0 - 0.001, closed_at=END0 - 0.0005)
        assert w["written"] and w["final"] is False
        rows = await _versions(conn, a, DAY0)
        assert [r["version"] for r in rows] == [1, 2, 3, 4, 5]
        assert not any(r["final"] for r in rows)
        assert all(r["reconciles"] for r in rows)
        # an unchanged intra-day write is still NO_CHANGE
        w = await PA.write_report(conn, session=sess,
                                  account_id=a["account_id"],
                                  now=END0 - 0.001)
        assert w["written"] is False and w["why"] == "NO_CHANGE"
        assert w["final"] is False
        assert len(await _versions(conn, a, DAY0)) == 5
    finally:
        await conn.close()


@pg
async def test_a_day_that_does_not_reconcile_is_final_and_says_so():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audbad", now=T)
        acct = a["account_id"]
        # an authoritative settlement version the ledger never credited: the
        # SETTLEMENTS check fails, so the day does not reconcile
        await conn.execute(
            "INSERT INTO paper_settlements (settlement_id, account_id, "
            " position_key, settlement_event_key, version, group_id, "
            " us_market_slug, holding_side, qty, outcome, payout_per_contract,"
            " payout_usd, evidence, evidence_source, settled_at) VALUES "
            " ($1,$2,$3,$4,1,$5,$6,'LONG',10,'WON',1,10,'{}'::jsonb,"
            "  'TEST_FIXTURE_UNCREDITED',to_timestamp($7))",
            "paperset:%s:orphan" % acct, acct,
            "paperpos:%s:orphan" % acct, "evt:%s" % acct,
            "paper_g_%s_orphan" % acct[-10:], "%s:orphan-mkt" % acct, T)
        r1 = await PA.step(conn, _ctx(a, T))
        assert r1["report"]["written"] and r1["report"]["version"] == 1
        found = await conn.fetchval(
            "SELECT count(*) FROM paper_audrey_findings WHERE session_id=$1 "
            "   AND kind='REPORT_DOES_NOT_RECONCILE' AND subject=$2",
            a["session_id"], str(DAY0))
        assert found == 1
        await PA.step(conn, _ctx(a, END0 + 30))
        rows = await _versions(conn, a, DAY0)
        assert [r["version"] for r in rows] == [1, 2]
        assert [r["final"] for r in rows] == [False, True]
        closing = rows[-1]
        assert closing["final"] is True and closing["reconciles"] is False
        rec = H.j(closing["report"])["reconciliation"]
        assert rec["reconciles"] is False
        failed = [c["check"] for c in rec["checks"] if not c["passed"]]
        assert failed == ["SETTLEMENTS_EQUAL_LEDGER_SETTLEMENT_AND_CORRECTIONS"]
        # the new day is open and still does not reconcile
        nd = await _versions(conn, a, DAY1)
        assert [(r["final"], r["reconciles"]) for r in nd] == [(False, False)]
    finally:
        await conn.close()
