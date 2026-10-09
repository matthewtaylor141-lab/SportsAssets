"""CAPITAL-CRITICAL (EVIDENCE): THE ATTRIBUTION READ HOLDS EVERY POSITION OF
EACH CONTROL'S POPULATION, OR SAYS BY NAME THAT IT DOES NOT (RC6.2 lane
p-evcontrols).

THE DEFECT. intel.attribution.load_paper read the newest 5,000 ENTER
decisions of the last 60 days and attributed only the ones that became a
position (an ENTRY paper order). Production, research-sql run 37943749921
(2026-10-09T14:23Z): 4,345 ENTER decisions in 60 days, 681 with an ENTRY
order; the quarantined PINNACLE_EXPLORATION_PAPER records ENTER verdicts the
ledger refuses (961, 542 and 636 on 10-07..10-09, 0 orders). At that rate
the orderless decisions fill the 5,000 around 2026-10-10 09:30Z and push
every real position out of the read: ATTRIBUTION turns RED on
NO_SETTLED_POSITION_CLAIMS_THE_IDENTITY, PROFIT_BREAKERS loses its
MECHANISM_DISABLED evidence and MULTIPLE_TESTING falls to tested = 0 -- the
failing members silently leave the acceptance population. Separately the
research study's plan declares no window, yet it was measured over the same
60-day read, so it would empty as its days aged out (about 2026-12-05).

THE FIX. The bound counts positions (ENTER decisions WITH an ENTRY order);
the read reports whether the bound left any position out (`truncated`, and
the newest one it left out); controls.attributed_positions reads every
position and each consumer takes its declared population: ATTRIBUTION and
PROFIT_BREAKERS the last 60 days, the study every position, the forward
scoreboard its cohort. A read that left out a member of a control's own
population turns that control RED by name (ATTRIBUTION_READ_TRUNCATED),
never a pass on a subset. No threshold, gate or population is narrowed.

  §1  load_paper at the production bound: positions older than 5,000
      orderless ENTER decisions are all read (b3f1b0cd: none); the PAPER
      loss attribution's 20,000 bound counts positions too
  §2  the read says exactly when its bound left a position out
  §3  the controls and the scoreboard fail closed by name on a truncated
      population, and stay as they were on a complete one
  §4  the readiness interlock end to end over a migrated database: the
      real positions reach ATTRIBUTION / PROFIT_BREAKERS / the study, the
      study reads a position older than 60 days that ATTRIBUTION's window
      does not, and a truncated read turns all three RED by name

ALL ROWS ARE SYNTHETIC TEST DATA written inside a transaction each test rolls
back; nothing is committed (the paper_acct_main rows of §4 included).
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from sportsassets.intel import attribution as A
from sportsassets.redteam import controls as C

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

CROWD_SQL = """
INSERT INTO paper_decisions (decision_id, session_id, account_id,
  decided_at, us_market_slug, holding_side, fixture, label, verdict,
  refusal, p_pinnacle, internal_model, pinnacle, limit_price, proposed_qty,
  economics, qualification_gaps, policy_version, simulator_version, strategy)
SELECT 'paperdec:crowd-' || $4 || '-' || g, $1, $2,
       to_timestamp($3 - g), 'crowd-mkt-' || $4 || '-' || g, 'LONG',
       'fx-crowd-' || $4 || '-' || g, '{}'::jsonb, 'ENTER', NULL, 0.5,
       '{}'::jsonb, '{}'::jsonb, 0.5, 10, '{}'::jsonb, '[]'::jsonb, 'TEST',
       $5, $6
  FROM generate_series(1, $7::int) g
"""


async def crowd(conn, acct, *, newest_at: float, n: int) -> None:
    """`n` ENTER decisions with NO order (the ledger refused them), the
    newest at `newest_at`, one second apart."""
    await conn.execute(CROWD_SQL, acct["session_id"], acct["account_id"],
                       float(newest_at), F.uid(""), F.SIM_VERSION,
                       F.STRATEGY, int(n))


async def settled_position(conn, acct, *, at: float, pnl_sign: int = 1,
                           slug=None) -> dict:
    """A settled PAPER position that claims the identity: decision (with a
    probability and a planned price) -> ENTRY order -> fill -> settlement."""
    d = await F.decision(conn, acct, at=at, p=0.6, limit=0.56, vwap=0.52,
                         qty=10, slug=slug)
    g = "paper_group_" + F.uid()
    oid = await F.order(conn, acct, group_id=g, slug=d["slug"], qty=10,
                        price=0.54, decision_id=d["decision_id"], at=at)
    await F.fill(conn, acct, order_id=oid, group_id=g, slug=d["slug"],
                 qty=10, price=0.54, fee=0.1, at=at + 2)
    await F.settle(conn, acct, group_id=g, slug=d["slug"], qty=10,
                   outcome="WON" if pnl_sign > 0 else "LOST",
                   payout_per_contract=1.0 if pnl_sign > 0 else 0.0,
                   at=at + 3600)
    return dict(d, group_id=g, at=at)


def _run(fn):
    import asyncpg

    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            return await fn(conn)
        finally:
            await tr.rollback()
            await conn.close()
    return asyncio.run(go())


# ── §1 the bound counts positions ────────────────────────────────────

@pg
def test_positions_older_than_5000_orderless_enter_decisions_are_all_read():
    """b3f1b0cd: the newest 5,000 ENTER decisions are the orderless ones,
    so the read returns NO position at all."""
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "evcread", now=now - 20 * 86400)
        pos = [await settled_position(conn, acct, at=now - 10 * 86400 + i)
               for i in range(3)]
        await crowd(conn, acct, newest_at=now - 60, n=5000)
        rows = await A.load_paper(conn, now=now,
                                  account_id=acct["account_id"])
        return pos, rows
    pos, rows = _run(fn)
    assert sorted(r["group_id"] for r in rows) == sorted(
        p["group_id"] for p in pos)
    assert all(r["identity_claimed"] for r in rows)


@pg
def test_the_loss_attribution_bound_counts_positions_and_names_its_read():
    """paper_loss_attribution's MAX_POSITIONS (20,000) counted ENTER
    decisions too (b3f1b0cd): 20,000 newer orderless ones and the PAPER
    loss report reads none of the book's positions."""
    from sportsassets import bettor_paper_session as S
    from sportsassets import paper_loss_attribution as PLA
    from sportsassets.intel import common as IC

    async def fn(conn):
        now = time.time()
        sess = await S.ensure_session(conn, account_id=IC.PAPER_ACCOUNT,
                                      now=now - 30 * 86400)
        acct = {"account_id": IC.PAPER_ACCOUNT,
                "session_id": sess["session_id"]}
        before = await conn.fetchval(
            "SELECT count(*) FROM paper_decisions d WHERE d.verdict = "
            "'ENTER' AND d.account_id = $1 AND EXISTS (SELECT 1 FROM "
            "paper_orders o WHERE o.decision_id = d.decision_id AND "
            "o.role = 'ENTRY')", IC.PAPER_ACCOUNT)
        await settled_position(conn, acct, at=now - 10 * 86400)
        await crowd(conn, acct, newest_at=now - 30,
                    n=PLA.MAX_POSITIONS)
        return int(before), await PLA.read(conn, now=now)
    before, got = _run(fn)
    assert got["positions"] == before + 1
    assert got["positions_read"]["truncated"] is False
    assert got["positions_read"]["complete"] is True
    assert got["positions_read"]["limit"] == PLA.MAX_POSITIONS


# ── §2 the read says exactly when its bound left a position out ──────

@pg
def test_the_read_reports_the_newest_position_its_bound_left_out():
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "evctrunc", now=now - 20 * 86400)
        pos = [await settled_position(conn, acct, at=now - 86400 * (k + 1))
               for k in range(4)]                    # newest first
        await crowd(conn, acct, newest_at=now - 60, n=50)
        full, part = {}, {}
        a = await A.load_paper(conn, now=now, account_id=acct["account_id"],
                               days=None, meta=full)
        b = await A.load_paper(conn, now=now, account_id=acct["account_id"],
                               days=None, limit=3, meta=part)
        return pos, a, full, b, part
    pos, a, full, b, part = _run(fn)
    assert len(a) == 4 and full["truncated"] is False
    assert full["positions_read"] == 4 and full["newest_unread_at"] is None
    assert full["window_days"] is None and full["since"] is None
    # the bound (3) left out the OLDEST position, and says which instant
    assert sorted(r["group_id"] for r in b) == sorted(
        p["group_id"] for p in pos[:3])
    assert part["truncated"] is True and part["positions_read"] == 3
    assert part["newest_unread_at"] == pytest.approx(pos[3]["at"], abs=1e-3)
    # a population that reaches back to the position left out is
    # incomplete; one that starts after it is complete; every-position is
    # incomplete
    cut = part["newest_unread_at"]                   # the stored instant
    assert A.read_complete_since(part, cut) is False
    assert A.read_complete_since(part, cut - 86400.0) is False
    assert A.read_complete_since(part, cut + 1.0) is True
    assert A.read_complete_since(part, None) is False
    assert A.read_complete_since(full, None) is True


def test_the_window_filter_is_the_sql_window():
    now = 1_791_555_792.0
    rows = [{"decided_at": now - 61 * 86400}, {"decided_at": now - 59 * 86400},
            {"decided_at": None}, {"decided_at": now}]
    w, rd = C.attribution_window(rows, {"truncated": False}, now=now)
    assert w == rows[1:2] + rows[3:]
    assert rd["population_window_days"] == C.ATTRIBUTION_WINDOW_DAYS == 60.0
    assert rd["complete"] is True
    assert A.within(rows, None) == rows


# ── §3 the controls fail closed by name ──────────────────────────────

def _claimed(g, pnl, at=1_791_000_000.0):
    return {"identity_claimed": True, "realized_pnl_usd": pnl,
            "model_edge_usd": pnl, "execution_edge_usd": 0.0,
            "management_usd": 0.0, "settlement_usd": 0.0,
            "outcome_variance_usd": 0.0, "group_id": g,
            "strategy": F.STRATEGY, "decided_at": at}


TRUNC = {"truncated": True, "newest_unread_at": 1_790_999_000.0,
         "positions_read": 5000, "limit": 5000}


def test_a_truncated_population_turns_attribution_red_by_name():
    rows = [_claimed("g1", 1.0), _claimed("g2", -2.0)]
    ok = C.attribution(rows, read=C.population_read(
        {"truncated": False}, since=None, window_days=None))
    assert ok["status"] == C.GREEN, ok
    bad = C.attribution(rows, read=C.population_read(
        TRUNC, since=1_790_000_000.0, window_days=60.0))
    assert bad["status"] == C.RED
    assert C.B_READ_TRUNCATED in bad["blockers"]
    assert bad["evidence"]["read"]["complete"] is False
    # the same read is complete for a population that starts after the
    # newest position it left out
    later = C.attribution(rows, read=C.population_read(
        TRUNC, since=1_790_999_500.0, window_days=60.0))
    assert later["status"] == C.GREEN and \
        later["evidence"]["read"]["complete"] is True


def test_a_truncated_population_turns_profit_breakers_and_the_study_red():
    mech = C.mechanism_rows([_claimed("g%d" % i, 1.0) for i in range(3)],
                            {})
    rd = C.population_read(TRUNC, since=None, window_days=None)
    pb = C.profit_breakers(mech, read=rd)
    assert pb["status"] == C.RED and C.B_READ_TRUNCATED in pb["blockers"]
    assert pb["evidence"]["read"]["complete"] is False
    reg = {"preregistered": 5, "candidates_tested": 3, "pbo_ok": True,
           "dsr_ok": True, "measurement": {}}
    assert C.multiple_testing(reg)["status"] == C.GREEN
    mt = C.multiple_testing(reg, read=rd)
    assert mt["status"] == C.RED and mt["blockers"] == [C.B_READ_TRUNCATED]
    # nothing describes the read: no blocker is invented, nothing relaxes
    assert C.multiple_testing(reg, read=None)["status"] == C.GREEN


def test_a_truncated_forward_cohort_is_no_scoreboard():
    from sportsassets.pm_bind import scoreboard as SB
    since = SB.cohort_start(SB.thresholds())
    rd = {"truncated": True, "newest_unread_at": since + 10.0,
          "positions_read": 5000}
    got = asyncio.run(SB.read(None, now=since + 86400, attributed=[],
                              fixtures={}, read=rd))
    assert got["status"] == "UNAVAILABLE"
    assert got["why"] == C.B_READ_TRUNCATED
    assert got["positions_read"]["complete"] is False
    assert got["positions_read"]["population_since"] == since


# ── §4 the readiness interlock end to end ────────────────────────────

@pg
def test_the_interlock_reads_every_position_of_each_population(monkeypatch):
    """paper_acct_main inside a rolled-back transaction: 3 settled positions
    in the last 60 days, 1 settled 70 days ago, 5,000 newer orderless ENTER
    decisions. b3f1b0cd: the read returns none of the positions, so
    ATTRIBUTION has no claim, PROFIT_BREAKERS no event and the study none of
    them; the plan declares no window, so the study also reads the 70-day-old
    position that ATTRIBUTION's 60-day window leaves out."""
    from sportsassets import bettor_paper_session as S
    from sportsassets.intel import common as IC
    from sportsassets.redteam import readiness as R
    from sportsassets.redteam import research_registry as RR

    seen = {}
    real_attr, real_measure = C.attribution, RR.measure

    def spy_attr(rows, *a, **k):
        seen["attribution"] = {r.get("group_id") for r in rows}
        return real_attr(rows, *a, **k)

    def spy_measure(reg, rows, fx):
        seen["study"] = {r.get("group_id") for r in rows}
        return real_measure(reg, rows, fx)
    monkeypatch.setattr(C, "attribution", spy_attr)
    monkeypatch.setattr(RR, "measure", spy_measure)

    async def fn(conn):
        now = time.time()
        sess = await S.ensure_session(conn, account_id=IC.PAPER_ACCOUNT,
                                      now=now - 90 * 86400)
        acct = {"account_id": IC.PAPER_ACCOUNT,
                "session_id": sess["session_id"]}
        before = await conn.fetchval(
            "SELECT count(*) FROM paper_decisions d WHERE d.verdict = "
            "'ENTER' AND d.account_id = $1 AND EXISTS (SELECT 1 FROM "
            "paper_orders o WHERE o.decision_id = d.decision_id AND "
            "o.role = 'ENTRY')", IC.PAPER_ACCOUNT)
        recent = [await settled_position(conn, acct,
                                         at=now - 5 * 86400 + i * 60)
                  for i in range(3)]
        old = await settled_position(conn, acct, at=now - 70 * 86400)
        await crowd(conn, acct, newest_at=now - 30, n=5000)
        await RR.ensure_preregistered(conn, implementation_sha="test",
                                      now=now)
        res = await R.evaluate(conn, now=now)
        first = dict(seen)
        # the same database with the read's bound forced below the
        # positions in the window: every control it feeds is RED by name
        real_load = A.load_paper

        async def small(conn_, **kw):
            return await real_load(conn_, **dict(kw, limit=2))
        monkeypatch.setattr(A, "load_paper", small)
        cut = await R.evaluate(conn, now=now)
        return int(before), recent, old, res, first, cut
    before, recent, old, res, first, cut = _run(fn)
    ctl = res["controls"]
    rg = {p["group_id"] for p in recent}
    # ATTRIBUTION / PROFIT_BREAKERS: the 60-day population, every member
    assert rg <= first["attribution"]
    assert old["group_id"] not in first["attribution"]
    at = ctl["ATTRIBUTION"]
    assert at["evidence"]["positions_identity_claimed"] >= 3, at
    assert "NO_SETTLED_POSITION_CLAIMS_THE_IDENTITY" not in at["blockers"]
    assert C.B_READ_TRUNCATED not in at["blockers"]
    assert at["evidence"]["read"]["complete"] is True
    assert at["evidence"]["read"]["population_window_days"] == 60.0
    assert at["evidence"]["read"]["positions_read"] == before + 4
    pb = ctl["PROFIT_BREAKERS"]["evidence"]["mechanisms"]["DIRECTIONAL"]
    assert pb["independent_events"] >= 3, pb
    # the study: every position, the 70-day-old one included
    assert rg | {old["group_id"]} <= first["study"]
    mt = ctl["MULTIPLE_TESTING"]
    assert mt["evidence"]["read"]["population_window_days"] is None
    assert mt["evidence"]["read"]["complete"] is True
    assert C.B_READ_TRUNCATED not in mt["blockers"]
    # the truncated pass
    for name in ("ATTRIBUTION", "PROFIT_BREAKERS", "MULTIPLE_TESTING"):
        c = cut["controls"][name]
        assert c["status"] == C.RED and C.B_READ_TRUNCATED in c["blockers"], \
            (name, c)
        assert c["evidence"]["read"]["complete"] is False
    assert res["status"] == cut["status"] == "PAPER_SHADOW_ONLY"
