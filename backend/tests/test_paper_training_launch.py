"""THE TRAINING LAUNCH CONFIGURATION, THROUGH THE REAL COLLECTION CYCLE.

The launch runs the completed-game INVESTMENT policy and the bounded
EXPLORATION strategy; MAKER ENTRY IS OFF (migration 189 inserts its row
disabled), so an unfilled resting bid can never occupy a fixture exploration
would train on.

  LAUNCH       the migration's own switches: maker off, exploration on.
  CHAIN        the REAL cycle (`ext_pinnacle_loop.cycle`, the harness of
               test_derek_enters_on_conservative_agreement: provider,
               resolver and venue quote substituted at their boundaries)
               writes a correctly matched valuation that the investment
               policy REFUSES after fees (0.5 pp gross at $0.50, 1.74 pp
               taker fee) and exploration ENTERS in the same in-cycle hook;
               the simulator fills it on a book observed AFTER the order
               became eligible (subsequent market evidence, not the decision
               book), the ledger debits the cost and fee, the group is handed
               to Xavier, Xavier reviews it and Audrey's fill audit passes.
               The same records reach the homepage read.
  NO DUPLICATES  the pass repeated, then the in-process state discarded (a
               restart) and the cycle and pass run again: one decision per
               strategy per valuation, one order, its fills, one handoff,
               one FILL ledger entry per fill.
  CONTENDERS   two strategies submitting entries on one fixture AT THE SAME
               TIME, on separate connections: fixture ownership is checked
               under the account lock, so exactly one reservation is written.

SYNTHETIC teams and books. No real money and no venue order.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import time

import pytest

from sportsassets import bettor_paper_experiment as EXP
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import pmus
from sportsassets import venue_pace
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import runtime as RT
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_completed_game_collector_path as CP
from tests import test_derek_enters_on_conservative_agreement as DT

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
CG, MAKER, EXPLORE = PB.CG_STRATEGY, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY
MIGRATION = (pathlib.Path(__file__).resolve().parents[1] / "migrations" /
             "189_paper_exploration_maker_and_operations_audit.sql")


def _migrated_switches() -> dict:
    sql = MIGRATION.read_text()
    out = {}
    for key in (MAKER, EXPLORE):
        m = re.search(r"\('%s',\s*(TRUE|FALSE)" % key, sql)
        assert m, key
        out[key] = m.group(1) == "TRUE"
    return out


def test_the_migration_launches_exploration_with_maker_off():
    assert _migrated_switches() == {MAKER: False, EXPLORE: True}


async def _nosleep(_):
    return None


async def _counts(conn, acct, slug) -> dict:
    sid = acct["session_id"]
    return {
        "decisions": [tuple(r) for r in await conn.fetch(
            "SELECT valuation_id, strategy, count(*) FROM paper_decisions "
            " WHERE session_id=$1 GROUP BY 1, 2 ORDER BY 1, 2", sid)],
        "orders": await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE session_id=$1 AND "
            " role='ENTRY'", sid),
        "fills": await conn.fetchval(
            "SELECT count(*) FROM paper_fills WHERE session_id=$1", sid),
        "fill_ledger": await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
            " kind='FILL'", acct["account_id"]),
        "submitted_ledger": await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
            " kind='ORDER_SUBMITTED'", acct["account_id"]),
        "handoffs": await conn.fetchval(
            "SELECT count(*) FROM paper_handoffs WHERE session_id=$1", sid),
        "funding": await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
            " kind='INITIAL_FUNDING'", acct["account_id"]),
    }


def _restart():
    """What a process restart discards: every in-process cache and queue
    (the database is what survives)."""
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    loop._RECENT_BOOKS.clear()
    loop.rules_cache_reset()
    PR._RETRY["tasks"].clear()
    PR._RETRY["recent"].clear()


@pg
async def test_the_launch_configuration_trains_through_the_real_cycle(
        monkeypatch):
    conn = await H.connect()
    t_start = time.time()
    real_rules = loop._read_venue_rules_blocking
    try:
        await PL.purge_everything(conn)
        await DT._seed(conn)
        acct = await PL.new_account(conn, "launch", now=t_start)
        t = PL.Transport(t_start)
        t.set(DT.US_SLUG, offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        monkeypatch.setattr(PR, "DEFAULT_ACCOUNT_ID", acct["account_id"])
        monkeypatch.setitem(PR._CLIENT, "client", PL.client(t))
        monkeypatch.setattr(RT, "paper_pass_hook",
                            lambda **kw: {"scheduled": False})
        monkeypatch.setenv(S.ENV_FLAG, "on")
        monkeypatch.setenv(PB.ENV_FLAG, "on")
        # THE LAUNCH SWITCHES: the investment policy on, the strict
        # benchmark off, and maker/exploration exactly as migrated
        sw = dict(_migrated_switches(), **{CG: True, PB.CONTROL_KEY: False})
        for key, on in sw.items():
            await conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                               " control_key=$1", key, on)
        _restart()
        # 0.505 against $0.50: 0.5 pp gross, the 1.74 pp taker fee eats it
        DT._stub(monkeypatch, p_home=0.505, stamp_age_s=2.0)
        # the REAL venue rules reader, on the venue's listing payload (as in
        # test_completed_game_collector_path): the grading period is read
        # from the venue's own words, not injected
        monkeypatch.setattr(loop, "_read_venue_rules_blocking", real_rules)
        monkeypatch.setattr(pmus, "_get_client", lambda: CP._Client())
        monkeypatch.setattr(venue_pace, "pace", lambda *a, **k: 0.0)

        out = await loop.cycle(conn)
        assert out.get("written", 0) >= 1, out.get("refusals")
        v = await conn.fetchrow(
            "SELECT id FROM external_valuations WHERE us_market_slug=$1 "
            " ORDER BY id DESC LIMIT 1", DT.US_SLUG)

        async def dec(strategy):
            return await conn.fetchrow(
                "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
                " valuation_id=$2 AND strategy=$3", acct["session_id"],
                v["id"], strategy)
        # THE INVESTMENT POLICY REFUSES AFTER FEES, unchanged
        cg = await dec(CG)
        assert cg is not None and cg["verdict"] == "REFUSE"
        assert cg["refusal"] in (PB.R_FEES, PB.R_FEES_CONSUME_EDGE), (
            cg["refusal"], cg["refusals"])
        assert H.j(cg["pinnacle"])["decided_via"] == PD.DECIDED_VIA_CYCLE
        # MAKER IS OFF: no decision, no order
        assert await dec(MAKER) is None
        # EXPLORATION ENTERS, in the same hook, on the same book
        ex = await dec(EXPLORE)
        assert ex is not None and ex["verdict"] == "ENTER", ex["refusals"]
        pdx = H.j(ex["policy_decision"])
        assert pdx["estimate"]["expected_net_profit_usd"] < 0
        assert pdx["training"] is True and pdx["training_purpose"]
        assert pdx["selection"]["selected"] is True
        assert ex["book_obs_id"] == cg["book_obs_id"], "one shared read"
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", ex["decision_id"])
        assert o["strategy"] == EXPLORE and o["state"] == \
            "PENDING_SIMULATION"
        # V3: no per-entry ceiling; the sizer still spends at most the
        # ~$1,000 target (budget = min(target, available cash)) per entry
        assert PEX.MAX_ENTRY_COST_USD is None
        assert float(o["reserved_usd"]) <= PEX.TARGET_ENTRY_COST_USD + 1e-6
        att = await conn.fetch(
            "SELECT strategy, outcome FROM paper_evaluation_attempts WHERE "
            " valuation_id=$1 ORDER BY attempt_id", v["id"])
        assert [(a["strategy"], a["outcome"]) for a in att] == [
            (CG, "DECIDED"), (EXPLORE, "DECIDED")]

        # THE SIMULATOR: a book observed AFTER the order became eligible
        eligible = L._epoch(o["eligible_at"])
        t.t = eligible + 1.0
        p1 = await PR.paper_pass(conn, now=eligible + 1.0,
                                 account_id=acct["account_id"],
                                 market_data=PL.client(t),
                                 config=acct["config"], force=True,
                                 sleep=_nosleep)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " order_id=$1", o["order_id"])
        assert o["state"] == "FILLED", o["state"]
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " order_id=$1", o["order_id"])
        assert fills
        for f in fills:
            assert f["book_obs_id"] is not None
            assert f["book_obs_id"] != ex["book_obs_id"], \
                "filled on the decision's own book"
            assert L._epoch(f["book_observed_at"]) >= eligible
        cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        fees = sum(float(f["fee_usd"]) for f in fills)
        assert cost + fees <= PEX.TARGET_ENTRY_COST_USD + 1e-6
        debit = await conn.fetchval(
            "SELECT -sum(cash_delta_usd) FROM paper_ledger WHERE "
            " order_id=$1 AND kind='FILL'", o["order_id"])
        assert float(debit) == pytest.approx(cost + fees)
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", o["group_id"])
        assert h is not None and h["strategy"] == EXPLORE
        assert h["owner"] == "XAVIER"

        # XAVIER MANAGES IT; AUDREY AUDITS THE CHAIN
        t.t = eligible + 6.0
        p2 = await PR.paper_pass(conn, now=eligible + 6.0,
                                 account_id=acct["account_id"],
                                 market_data=PL.client(t),
                                 config=acct["config"], force=True,
                                 sleep=_nosleep)
        assert not p2["errors"], p2["errors"]
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " group_id=$1 ORDER BY reviewed_at LIMIT 1",
                                 o["group_id"])
        assert rv is not None and rv["strategy"] == EXPLORE
        fa = await conn.fetchrow(
            "SELECT * FROM paper_audrey_findings WHERE account_id=$1 AND "
            " kind=$2 AND subject=$3", acct["account_id"],
            PB.EXPLORE_POLICY["audit_kind"], o["group_id"])
        assert fa is not None and H.j(fa["detail"])["passed"] is True
        b = await L.balances(conn, acct["account_id"], now=eligible + 7)
        assert b["ledger_consistent"] is True
        assert b["cash_usd"] == pytest.approx(500000.0 - cost - fees)

        # THE HOMEPAGE READ: the same records
        x = await EXP.experiment(conn, now=eligible + 8,
                                 account_id=acct["account_id"])
        assert x["state"]["connected"] is True
        pos = [r for r in x["positions"]["data"]["open_positions"]
               if r["group_id"] == o["group_id"]]
        assert pos and pos[0]["label"] == "Training / simulated execution"
        xf = {r["fill_id"] for r in x["fills"]["data"]}
        assert {f["fill_id"] for f in fills} <= xf

        # NO DUPLICATION: the pass again, then a restart, cycle and pass
        before = await _counts(conn, acct, DT.US_SLUG)
        assert before["orders"] == 1 and before["handoffs"] == 1
        assert before["fill_ledger"] == before["fills"] == len(fills)
        assert before["submitted_ledger"] == 1 and before["funding"] == 1
        await PR.paper_pass(conn, now=eligible + 9.0,
                            account_id=acct["account_id"],
                            market_data=PL.client(t), config=acct["config"],
                            force=True, sleep=_nosleep)
        assert await _counts(conn, acct, DT.US_SLUG) == before
        _restart()
        # the same valuation decided again (the pass backstop path)
        again = await PR.decide_valuation(
            conn, valuation_id=v["id"], market_data=PL.client(t),
            account_id=acct["account_id"],
            schedule_fill=lambda: {"scheduled": False})
        assert again.get("decided") is not False or again.get("why")
        await PR.paper_pass(conn, now=eligible + 12.0,
                            account_id=acct["account_id"],
                            market_data=PL.client(t), config=acct["config"],
                            force=True, sleep=_nosleep)
        after = await _counts(conn, acct, DT.US_SLUG)
        assert after == before, (before, after)
        # a NEW valuation of the same fixture after the restart: exploration
        # refuses (one position per fixture), the investment policy refuses
        # (fees, and the fixture is exploration's) -- still one order
        _restart()
        await loop.cycle(conn)
        v2 = await conn.fetchval(
            "SELECT max(id) FROM external_valuations WHERE "
            " us_market_slug=$1", DT.US_SLUG)
        if v2 != v["id"]:
            ex2 = await conn.fetchrow(
                "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
                " valuation_id=$2 AND strategy=$3", acct["session_id"], v2,
                EXPLORE)
            assert ex2 is None or ex2["verdict"] == "REFUSE"
        final = await _counts(conn, acct, DT.US_SLUG)
        assert final["orders"] == 1 and final["handoffs"] == 1
        assert final["fills"] == before["fills"]
        assert final["fill_ledger"] == before["fill_ledger"]
        assert final["funding"] == 1
    finally:
        _restart()
        await PL.drop_today_run(conn, t_start)
        await DT._cleanup(conn)
        await PL.purge_everything(conn)
        for key, on in dict(_migrated_switches(),
                            **{CG: True, PB.CONTROL_KEY: False}).items():
            await conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                               " control_key=$1", key, on)
        await conn.close()


def _entry(acct, *, strategy, key, slug, fixture, at):
    return {"idempotency_key": key, "account_id": acct["account_id"],
            "session_id": acct["session_id"], "group_id": "paperg:" + key,
            "role": "ENTRY", "direction": "BUY", "holding_side": "LONG",
            "intent": "LONG", "us_market_slug": slug, "fixture": fixture,
            "label": {}, "order_type": "MARKETABLE", "time_in_force": "IOC",
            "allow_partial": True, "qty": 10, "limit_price": 0.5,
            "wire_price": 0.5, "decision_id": None, "decided_at": at,
            "eligible_at": at + 2, "expires_at": at + 30,
            "simulator_version": "test", "strategy": strategy}


@pg
async def test_simultaneous_contenders_for_one_fixture_reserve_once():
    """Two strategies' entries on ONE fixture, submitted concurrently on two
    connections, each past every pre-lock check: the ownership check under
    the account lock admits exactly one. Repeated, and with the maker's own
    one-live-entry rule."""
    conn = await H.connect()
    c1, c2 = await H.connect(), await H.connect()
    now = time.time()
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "contend", now=now)
        run = "%d" % int(now * 1000)
        for i in range(5):
            fx = "condition:contend-%s-%d" % (run, i)
            a = _entry(acct, strategy=CG, key="paperk:cg:%s:%d" % (run, i),
                       slug="aec-contend-%s-%d-a" % (run, i), fixture=fx, at=now)
            b = _entry(acct, strategy=EXPLORE, key="paperk:ex:%s:%d" % (run, i),
                       slug="aec-contend-%s-%d-b" % (run, i), fixture=fx, at=now)
            r = await asyncio.gather(
                L.submit_order(c1, a, now=now, exclusive_fixture=True),
                L.submit_order(c2, b, now=now, exclusive_fixture=True))
            oks = [x for x in r if x.get("ok")]
            assert len(oks) == 1, r
            lost = [x for x in r if not x.get("ok")][0]
            assert lost["refusal"] == L.R_FIXTURE_OWNED
            assert lost["under_lock"] is True
            n = await conn.fetchval(
                "SELECT count(*) FROM paper_orders WHERE account_id=$1 AND "
                " fixture=$2", acct["account_id"], fx)
            assert n == 1
        # THE MAKER'S ONE LIVE ENTRY PER FIXTURE, under the lock as well
        fx = "condition:contend-maker-%s" % run
        m = [dict(_entry(acct, strategy=MAKER, key="paperk:mk:%s:%d" % (run, i),
                         slug="aec-contend-mk-%s" % run, fixture=fx, at=now),
                  order_type="RESTING", time_in_force="GTD")
             for i in range(2)]
        r = await asyncio.gather(
            L.submit_order(c1, m[0], now=now, exclusive_fixture=True,
                           one_live_entry_per_fixture=True),
            L.submit_order(c2, m[1], now=now, exclusive_fixture=True,
                           one_live_entry_per_fixture=True))
        assert sum(1 for x in r if x.get("ok")) == 1, r
        assert [x for x in r if not x.get("ok")][0]["refusal"] == \
            L.R_SAME_STRATEGY_LIVE
        # cash: reservations for exactly the admitted orders
        b = await L.balances(conn, acct["account_id"], now=now + 1)
        assert b["ledger_consistent"] is True
        live = await conn.fetchval(
            "SELECT sum(reserved_remaining_usd) FROM paper_orders WHERE "
            " account_id=$1", acct["account_id"])
        assert b["reserved_usd"] == pytest.approx(float(live))
    finally:
        await PL.purge_everything(conn)
        await c1.close()
        await c2.close()
        await conn.close()
