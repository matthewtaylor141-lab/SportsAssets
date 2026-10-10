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
  §5  (review rework 2) the WHOLE population, a page at a time: the study
      and the forward scoreboard read every member past 5,000 (e4995085:
      RED / UNAVAILABLE for good from the 5,001st), a paged read equals the
      one-shot read row for row, and a cut is possible only at the safety
      stop, still by name wherever it falls on a page

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


# ── §5 the whole population, a page at a time (review rework 2) ─────
#
# THE CLIFF (review of e4995085). controls.attributed_positions reads EVERY
# position (days=None) for the MULTIPLE_TESTING study (its frozen plan
# declares no window) and the forward scoreboard (every position since the
# thresholds' frozen_at). Both populations only grow, and a cut read fails
# closed. Under the 5,000 bound the study was RED by ATTRIBUTION_READ_
# TRUNCATED for good from the 5,001st lifetime position, and the scoreboard
# UNAVAILABLE once 5,000 positions followed 2026-10-07 -- whatever the data
# said; scorecard_14 category 8 labels a failing MULTIPLE_TESTING forward
# evidence. load_paper now pages the whole population through a cursor; a
# cut is possible only at the safety stop PAPER_POSITIONS_LIMIT (20,000).

BULK_DEC_SQL = """
INSERT INTO paper_decisions (decision_id, session_id, account_id,
  decided_at, us_market_slug, holding_side, fixture, label, verdict,
  refusal, p_pinnacle, internal_model, pinnacle, limit_price, proposed_qty,
  economics, qualification_gaps, policy_version, simulator_version, strategy)
SELECT 'paperdec:life-' || $4 || '-' || g, $1, $2,
       to_timestamp($3 - g), 'life-mkt-' || $4 || '-' || g, 'LONG',
       'fx-life-' || $4 || '-' || g, '{}'::jsonb, 'ENTER', NULL, 0.5,
       '{}'::jsonb, '{}'::jsonb, 0.5, 10, '{}'::jsonb, '[]'::jsonb, 'TEST',
       $5, $6
  FROM generate_series(1, $7::int) g
"""
BULK_ORD_SQL = """
INSERT INTO paper_orders (order_id, idempotency_key, account_id, session_id,
  group_id, role, direction, holding_side, intent, us_market_slug, fixture,
  label, order_type, time_in_force, allow_partial, qty, limit_price,
  wire_price, filled_qty, state, decision_id, decided_at, eligible_at,
  expires_at, simulator_version, strategy, terminal_at, terminal_reason)
SELECT 'paperord:life-' || $1 || '-' || g, 'paperord:life-' || $1 || '-' || g,
       $2, $3, 'paper_group_life-' || $1 || '-' || g, 'ENTRY', 'BUY', 'LONG',
       'ORDER_INTENT_BUY_LONG', 'life-mkt-' || $1 || '-' || g,
       'fx-life-' || $1 || '-' || g, '{}'::jsonb, 'MARKETABLE', 'IOC', true,
       10, 0.50, 0.50, 0, 'EXPIRED', 'paperdec:life-' || $1 || '-' || g,
       to_timestamp($4 - g), to_timestamp($4 - g + 1),
       to_timestamp($4 - g + 90), $5, $6, to_timestamp($4 - g + 90), 'TEST'
  FROM generate_series(1, $7::int) g
"""


async def bulk_positions(conn, acct, *, newest_at: float, n: int) -> None:
    """`n` positions (ENTER decision + ENTRY order, never filled), the newest
    at `newest_at`, one second apart: the population's members, cheaply."""
    tag = F.uid("")
    await conn.execute(BULK_DEC_SQL, acct["session_id"], acct["account_id"],
                       float(newest_at), tag, F.SIM_VERSION, F.STRATEGY,
                       int(n))
    await conn.execute(BULK_ORD_SQL, tag, acct["account_id"],
                       acct["session_id"], float(newest_at), F.SIM_VERSION,
                       F.STRATEGY, int(n))


async def _paper_main(conn, now):
    from sportsassets import bettor_paper_session as S
    from sportsassets.intel import common as IC
    sess = await S.ensure_session(conn, account_id=IC.PAPER_ACCOUNT,
                                  now=now - 400 * 86400)
    return {"account_id": IC.PAPER_ACCOUNT, "session_id": sess["session_id"]}


_POSITIONS_SQL = (
    "SELECT count(*) FROM paper_decisions d WHERE d.verdict = 'ENTER' AND "
    "d.account_id = $1 AND EXISTS (SELECT 1 FROM paper_orders o WHERE "
    "o.decision_id = d.decision_id AND o.role = 'ENTRY')")

STUDY_REG = {"preregistered": 5, "candidates_tested": 0}


@pg
def test_5001_lifetime_positions_outside_60_days_keep_the_study_whole():
    """The review's demonstration: 5,001 positions, all 90+ days old.
    e4995085: the read is cut at 5,000, the 60-day controls are whole, and
    the study is RED by ATTRIBUTION_READ_TRUNCATED -- for good, since the
    population only grows. Now: every position is read; nothing is cut."""
    async def fn(conn):
        now = time.time()
        acct = await _paper_main(conn, now)
        await bulk_positions(conn, acct, newest_at=now - 90 * 86400,
                             n=5001)
        before = int(await conn.fetchval(_POSITIONS_SQL,
                                         acct["account_id"]))
        aread: dict = {}
        every, _fx = await C.attributed_positions(conn, now=now,
                                                  detail=aread)
        return now, before, aread, every
    now, population, aread, every = _run(fn)
    assert population >= 5001
    assert aread["truncated"] is False, aread
    assert aread["positions_read"] == len(every) == population
    assert aread["limit"] == A.PAPER_POSITIONS_LIMIT >= 20000
    assert aread["pages"] == -(-population // A.PAPER_PAGE_POSITIONS)
    win, wread = C.attribution_window(every, aread, now=now)
    assert wread["complete"] is True
    mt_read = C.population_read(aread, since=None, window_days=None)
    assert mt_read["complete"] is True
    mt = C.multiple_testing(STUDY_REG, read=mt_read)
    assert C.B_READ_TRUNCATED not in mt["blockers"], mt["blockers"]
    at = C.attribution(win, read=wread)
    assert C.B_READ_TRUNCATED not in at["blockers"]


@pg
def test_5001_positions_in_the_forward_cohort_still_make_a_scoreboard():
    """5,001 positions after the thresholds' frozen_at. e4995085: the read
    is cut, the newest position left out is inside the cohort, and the
    forward scoreboard is UNAVAILABLE (ATTRIBUTION_READ_TRUNCATED) -- for
    good, since the cohort only grows. Now: the cohort is read whole."""
    from sportsassets.pm_bind import scoreboard as SB

    async def fn(conn):
        now = time.time()
        since = SB.cohort_start(SB.thresholds())
        acct = await _paper_main(conn, now)
        await bulk_positions(conn, acct, newest_at=since + 5001 + 60,
                             n=5001)
        aread: dict = {}
        every, fx = await C.attributed_positions(conn, now=now,
                                                 detail=aread)
        sb = await SB.read(conn, now=now, attributed=every, fixtures=fx,
                           read=aread)
        return since, aread, every, sb
    since, aread, every, sb = _run(fn)
    assert sb.get("status") != "UNAVAILABLE", (sb.get("why"),
                                               sb.get("positions_read"))
    assert aread["truncated"] is False, aread
    cohort = [r for r in every if (r.get("decided_at") or 0) >= since]
    assert len(cohort) >= 5001
    assert sb["positions_read"]["complete"] is True
    assert sb["positions_read"]["population_since"] == since


@pg
def test_a_paged_read_equals_the_one_shot_read_row_for_row():
    """Pages of 2 against one page holding everything: the same rows in the
    same order (decided_at, decision_id descending -- ties included), the
    same fills, settlements, Xavier action counts and valuations per
    position, whichever page each lands on."""
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "evcpage", now=now - 20 * 86400)
        pos = [await settled_position(conn, acct, at=now - 86400 * (k + 1),
                                      pnl_sign=1 if k % 2 else -1)
               for k in range(5)]
        # a tie: two positions decided at the same instant
        tie = now - 86400 * 2
        pos.append(await settled_position(conn, acct, at=tie))
        for k, p in enumerate(pos[:3]):
            for j in range(k + 1):
                await conn.execute(
                    "INSERT INTO paper_xavier_reviews (review_id, session_id,"
                    " account_id, group_id, reviewed_at, trigger,"
                    " alternatives, exposure, action, strategy) VALUES"
                    " ($1, $2, $3, $4, to_timestamp($5),"
                    " 'SCHEDULED_BACKSTOP', '[]', '{}',"
                    " '{\"kind\": \"HOLD\"}', $6)",
                    "paperrev:" + F.uid(""), acct["session_id"],
                    acct["account_id"], p["group_id"], p["at"] + 60 + j,
                    F.STRATEGY)
        one, paged = {}, {}
        a = await A.load_paper(conn, now=now, account_id=acct["account_id"],
                               days=None, meta=one)
        b = await A.load_paper(conn, now=now, account_id=acct["account_id"],
                               days=None, meta=paged, page=2)
        return pos, a, one, b, paged
    pos, a, one, b, paged = _run(fn)
    assert len(a) == len(pos) == 6
    assert a == b
    assert sorted(r["management_actions"] for r in a) == [0, 0, 0, 1, 2, 3]
    assert one["pages"] == 1 and paged["pages"] == 3
    assert paged["page_positions"] == 2
    for k in ("positions_read", "truncated", "newest_unread_at",
              "oldest_read_at", "order"):
        assert one[k] == paged[k], k
    ats = [(r["decided_at"], r["decision_id"]) for r in a]
    assert ats == sorted(ats, reverse=True)


@pg
def test_the_safety_stop_still_fails_closed_by_name_on_any_page_edge():
    """A cut is possible only at the stop, and it is exact wherever the stop
    falls: mid-page (stop 3, pages of 2) or on a page edge (stop 4, pages
    of 2). The OLDEST positions are the ones left out, and the read names
    the newest of them."""
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "evcstop", now=now - 20 * 86400)
        pos = [await settled_position(conn, acct, at=now - 86400 * (k + 1))
               for k in range(6)]                    # newest first
        out = {}
        for stop in (3, 4, 6):
            m: dict = {}
            rows = await A.load_paper(conn, now=now,
                                      account_id=acct["account_id"],
                                      days=None, limit=stop, page=2, meta=m)
            out[stop] = (rows, m)
        return pos, out
    pos, out = _run(fn)
    for stop in (3, 4):
        rows, m = out[stop]
        assert sorted(r["group_id"] for r in rows) == sorted(
            p["group_id"] for p in pos[:stop])
        assert m["truncated"] is True and m["positions_read"] == stop
        assert m["newest_unread_at"] == pytest.approx(pos[stop]["at"],
                                                      abs=1e-3)
        assert A.read_complete_since(m, None) is False
        rd = C.population_read(m, since=None, window_days=None)
        mt = C.multiple_testing(STUDY_REG, read=rd)
        assert C.B_READ_TRUNCATED in mt["blockers"]
    rows, m = out[6]                    # the stop equals the population
    assert len(rows) == 6 and m["truncated"] is False
    assert m["newest_unread_at"] is None


def test_the_read_pages_through_a_cursor_and_the_cycle_keeps_its_bound():
    import inspect
    from sportsassets.intel import runner as RUN
    src = inspect.getsource(A.load_paper)
    assert "conn.cursor(" in src and "cur.fetch(page)" in src
    assert "C.offload(paper_rows," in src
    # the stop is a safety stop far above production's whole population
    # (681 positions, research-sql 37979576920), not a sample size
    assert A.PAPER_POSITIONS_LIMIT == 20000
    assert A.PAPER_PAGE_POSITIONS == 1000
    # the intel shadow cycle (a display snapshot) keeps its earlier bound
    assert A.INTEL_CYCLE_POSITIONS_LIMIT == 5000
    assert "limit=AT.INTEL_CYCLE_POSITIONS_LIMIT" in inspect.getsource(
        RUN.run_cycle)
