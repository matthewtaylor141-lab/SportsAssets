"""PAPER CAPITAL AUTHORITY AND ZERO-CAPITAL SHADOW LEARNING (migration 305).

Runs the PRODUCTION gate (CAPITAL_AUTHORITY_ENFORCED below: the suite's
seeded forward economics in tests/conftest.py does not apply here).

  §1 the forward-economics rule (pure): UNKNOWN below the declared sample,
     NEGATIVE on a non-positive net or on negative realized PAPER evidence
     alone, POSITIVE otherwise; the forward window starts at the epoch.
  §2 a SHADOW_ONLY strategy places NO paper order; its decision becomes a
     SHADOW_COUNTERFACTUAL with the depth walk, per-level fees, the delay and
     the NOT_REALIZED_PNL label -- at the decision gate and at the ledger.
  §3 forward economics UNKNOWN / NEGATIVE refuse by name and route to shadow;
     settled shadow evidence makes them POSITIVE without touching PAPER P&L.
  §4 a stopping rule firing AT THE ENTRY refuses, though the stored state
     still permits entry.
  §5 an ENTRY without evidence, or with non-positive executable EV, is
     refused for explore and maker too.
  §6 every refusal row carries the instrumentation; the distinct contract /
     side census ranks them.
  §7 the acceptance read: its shape, its assertions, its authority.
  §8 the tables are append-only and labelled; the module reaches no funded,
     venue or order-execution path; the proof is capital-critical.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

from sportsassets import bettor_capital_authority as CA
from sportsassets import bettor_capital_eligibility as CE
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_strategy_lifecycle as LC

try:
    from tests import paper_harness as H
except ImportError:                                           # pragma: no cover
    import paper_harness as H

CAPITAL_AUTHORITY_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = 1_791_500_000.0
DEREK = "DEREK_ENTRY_POLICY_V2"
CG = "PINNACLE_COMPLETED_GAME_PAPER"
EXPLORE = "PINNACLE_EXPLORATION_PAPER"
MAKER = "PINNACLE_COMPLETED_GAME_MAKER_PAPER"
BENCH = "PINNACLE_ONLY_PAPER_BENCHMARK"
FEE = H.flat_fee(0.01)


# ── §1 the forward-economics rule ────────────────────────────────────

def test_unknown_below_the_declared_sample():
    n = CA.MIN_FORWARD_OBSERVATIONS
    assert n == LC.MIN_CLOSED_FOR_RATE_RULES == 20
    v = CA.forward_verdict([], [])
    assert v["verdict"] == CA.UNKNOWN and v["refusal"] == CA.R_FORWARD_UNKNOWN
    assert v["net_pnl_usd"] is None                 # no basis: never zero
    v = CA.forward_verdict([50.0] * 10, [50.0] * (n - 11))
    assert v["verdict"] == CA.UNKNOWN and v["observations"] == n - 1


def test_negative_and_positive():
    n = CA.MIN_FORWARD_OBSERVATIONS
    assert CA.forward_verdict([-1.0] * n, [])["verdict"] == CA.NEGATIVE
    assert CA.forward_verdict([0.0] * n, [])["verdict"] == CA.NEGATIVE
    # shadow gains never outweigh realized PAPER losses on their own sample
    v = CA.forward_verdict([-1.0] * n, [100.0] * n)
    assert v["verdict"] == CA.NEGATIVE
    assert v["refusal"] == CA.R_FORWARD_NEGATIVE
    v = CA.forward_verdict([-1.0] * 5, [2.0] * (n - 5))
    assert v["verdict"] == CA.POSITIVE and v["refusal"] is None
    assert v["realized_paper"]["pnl_class"] == "REALIZED_PAPER_PNL"
    assert v["shadow"]["pnl_class"] == CA.PNL_CLASS == "NOT_REALIZED_PNL"


def test_the_forward_window_starts_at_the_epoch_and_counts_only_closed():
    from sportsassets import bettor_paper_epoch as EP
    assert CA.MANAGEMENT_EPOCH_START == EP.EPOCH_START
    assert CA.FORWARD_SINCE == max(EP.EPOCH_START, LC.RULES_DECLARED_AT)
    s = CA.FORWARD_SINCE
    pos = [
        {"strategy": CG, "first_fill_at": s - 10, "open_qty": 0,
         "last_fill_at": s, "realized_pnl_usd": 99.0},       # pre-epoch
        {"strategy": CG, "first_fill_at": s + 10, "open_qty": 5,
         "realized_pnl_usd": 0.0},                            # open
        {"strategy": CG, "first_fill_at": s + 10, "open_qty": 0,
         "last_fill_at": s + 20, "realized_pnl_usd": -7.0},   # counted
        {"strategy": BENCH, "first_fill_at": s + 10, "open_qty": 0,
         "last_fill_at": s + 20, "realized_pnl_usd": 3.0}]    # other
    assert CA.forward_paper_pnls(pos, CG) == [-7.0]


def test_every_new_gate_only_refuses():
    # no capital-authority code is an admission or a size; every one is a
    # registered refusal
    from sportsassets import refusal_taxonomy_table as TT
    for code in (CA.R_FORWARD_UNKNOWN, CA.R_FORWARD_NEGATIVE,
                 CA.R_FORWARD_UNREADABLE, CA.R_RULE_FIRING_AT_ENTRY,
                 CA.R_RULES_UNREADABLE, CA.R_EV_NOT_EVIDENCED):
        assert code in TT.TABLE, code
    assert set(LC.ENTRY_STATE_REFUSAL.values()) <= set(
        CA.NO_CAPITAL_AUTHORITY)
    src = (PKG / "bettor_capital_authority.py").read_text()
    for word in ("per_order_cap_usd", "per_market_cap_usd",
                 "REDUCED_SIZE_FACTOR =", "transition(", "LC.record("):
        assert word not in src, word


# ── helpers ──────────────────────────────────────────────────────────

async def _tx():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    return conn, tr


async def _done(conn, tr):
    await tr.rollback()
    await conn.close()


async def _state(conn, acct, strategy, to, *, frm=None):
    return await LC.record(conn, account_id=acct, strategy=strategy,
                           from_state=frm, to_state=to, rule_id="TEST",
                           actor="person:test", evidence={"t": 1},
                           why="test", at=NOW - 30)


def _cand(slug):
    return {"us_market_slug": slug, "payout_event": "HOME",
            "fixture": "fx-" + slug, "settlement": {"compatibility":
                                                    "COMPATIBLE"}}


def _evidence(*, p, levels, qty, limit, slug, fee_fn=FEE, at=NOW):
    ce = CA.evaluate_executable(
        p=p, levels=levels, qty=qty, limit=limit, fee_fn=fee_fn, at=at,
        settlement={"compatibility": "COMPATIBLE"},
        identity={"us_market_slug": slug, "payout_event": "HOME",
                  "fixture": "fx-" + slug, "holding_side": "LONG"})
    return CA.capital_evidence(ce, p=p, limit=limit, threshold_edge_pp=0.5,
                               basis="TEST", levels=levels)


def _order(a, *, key, strategy, qty=100, limit=0.40, p=0.60, at=NOW,
           evidence=True, order_type="MARKETABLE"):
    slug = "%s:%s" % (a["account_id"], key)
    o = H.order(a, key=key, qty=qty, limit=limit, at=at, slug=slug,
                group_id="paper_g_%s_%s" % (a["account_id"][-6:], key),
                fixture="fx-" + slug, order_type=order_type,
                tif="GTD" if order_type == "RESTING" else "IOC",
                queue_ahead=0.0 if order_type == "RESTING" else None)
    o["strategy"] = strategy
    if evidence:
        o["capital_evidence"] = _evidence(
            p=p, levels=[{"price": limit, "qty": qty}], qty=qty, limit=limit,
            slug=slug, at=at)
    return o


async def _counts(conn, acct):
    return {k: await conn.fetchval(sql, acct) for k, sql in {
        "orders": "SELECT count(*) FROM paper_orders WHERE account_id=$1",
        "ledger": "SELECT count(*) FROM paper_ledger WHERE account_id=$1",
        "shadows": "SELECT count(*) FROM paper_shadow_counterfactuals "
                   " WHERE account_id=$1",
        "census": "SELECT count(*) FROM paper_entry_refusal_census "
                  " WHERE account_id=$1"}.items()}


def _settled(outcome, payout):
    async def fn(conn, slug, side):
        return {"outcome": outcome, "payout_per_contract": payout,
                "evidence": {"test": True}}
    return fn


async def _seed_shadows(conn, a, strategy, n, *, outcome="WON", payout=1.0,
                        p=0.60, limit=0.40, qty=100):
    for i in range(n):
        slug = "%s:seed:%s:%d" % (a["account_id"], strategy[-6:], i)
        ev = _evidence(p=p, levels=[{"price": limit, "qty": qty}], qty=qty,
                       limit=limit, slug=slug)
        got = await CA.record_shadow(
            conn, account_id=a["account_id"], strategy=strategy,
            decision_id="dec:%s:%d" % (slug, i), order_key=None,
            source=CA.SRC_DECISION, capital_refusal=CA.R_FORWARD_UNKNOWN,
            lifecycle_state=LC.ACTIVE_CHALLENGER, slug=slug,
            holding_side="LONG", fixture="fx-" + slug, payout_event="HOME",
            decided_at=NOW - 600 + i, delay_s=2.0, expires_at=NOW - 500 + i,
            simulator_version="test", evidence=ev)
        assert got["recorded"], got
    return await CA.settle_shadows(conn, now=NOW, account_id=a["account_id"],
                                   settlement_fn=_settled(outcome, payout))


# ── §2 SHADOW_ONLY: no paper order, a shadow counterfactual ──────────

@pg
async def test_shadow_only_places_no_order_and_records_the_counterfactual():
    from sportsassets.agents import paper_derek as PD
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "cash", now=NOW - 60)
        acct = a["account_id"]
        await _state(conn, acct, DEREK, LC.SHADOW_ONLY)
        ctx = {"account_id": acct, "config": a["config"]}
        levels = [{"price": 0.40, "qty": 100}, {"price": 0.41, "qty": 200}]
        ce = await PD.capital_gate(
            conn, ctx, strategy=DEREK, p=0.60, levels=levels,
            sized={"qty": 250, "limit": 0.41}, cand=_cand("m-shadow"),
            side="LONG", at=NOW, fee_fn=FEE, decision_id="paperdec:shadow1",
            threshold_edge_pp=0.5)
        assert ce["capital_eligible"] is False
        assert ce["refusals"] == [LC.R_LIFECYCLE_SHADOW_ONLY]
        assert ce["shadow"]["recorded"] is True
        assert ce["shadow"]["pnl_class"] == "NOT_REALIZED_PNL"
        assert (await _counts(conn, acct))["orders"] == 0
        r = await conn.fetchrow(
            "SELECT * FROM paper_shadow_counterfactuals WHERE shadow_id=$1",
            ce["shadow"]["shadow_id"])
        assert r["evidence_class"] == "SHADOW_COUNTERFACTUAL"
        assert r["pnl_class"] == "NOT_REALIZED_PNL"
        assert r["source"] == CA.SRC_DECISION
        assert r["capital_refusal"] == LC.R_LIFECYCLE_SHADOW_ONLY
        assert r["lifecycle_state"] == LC.SHADOW_ONLY
        # the SAME decision instant, the simulator's delay
        assert L._epoch(r["decided_at"]) == NOW
        delay = a["config"]["simulator"]["decision_to_execution_delay_s"]
        assert float(r["decision_to_execution_delay_s"]) == delay
        assert L._epoch(r["eligible_at"]) == NOW + delay
        # the depth walk at the INTENDED size, per-level fees, adverse sel.
        fills = H.j(r["fills"])
        assert fills == [[0.40, 100.0], [0.41, 150.0]]
        assert float(r["qty"]) == 250
        assert float(r["fees_usd"]) == pytest.approx(2.5)        # 250 x 0.01
        assert float(r["cost_usd"]) == pytest.approx(40.0 + 61.5)
        vwap = 101.5 / 250
        assert float(r["adverse_selection_usd"]) == pytest.approx(
            (0.41 - vwap) * 250)
        assert float(r["total_executable_ev_usd"]) == pytest.approx(
            250 * 0.60 - 101.5 - 2.5 - (0.41 - vwap) * 250)
        # idempotent per decision
        again = await PD.capital_gate(
            conn, ctx, strategy=DEREK, p=0.60, levels=levels,
            sized={"qty": 250, "limit": 0.41}, cand=_cand("m-shadow"),
            side="LONG", at=NOW, fee_fn=FEE, decision_id="paperdec:shadow1")
        assert again["shadow"]["duplicate"] is True
        # a decision that fails something OTHER than capital authority is
        # not a counterfactual (here: negative executable EV)
        neg = await PD.capital_gate(
            conn, ctx, strategy=DEREK, p=0.30, levels=levels,
            sized={"qty": 250, "limit": 0.41}, cand=_cand("m-neg"),
            side="LONG", at=NOW, fee_fn=FEE, decision_id="paperdec:neg")
        assert neg["shadow"]["recorded"] is False
        assert (await _counts(conn, acct))["shadows"] == 1
    finally:
        await _done(conn, tr)


@pg
async def test_shadow_only_at_the_ledger_records_shadow_and_census_not_an_order():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "casl", now=NOW - 60)
        acct = a["account_id"]
        await _state(conn, acct, EXPLORE, LC.SHADOW_ONLY)
        before = await _counts(conn, acct)
        o = _order(a, key="s1", strategy=EXPLORE)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"] is False
        assert got["refusal"] == LC.R_LIFECYCLE_SHADOW_ONLY
        after = await _counts(conn, acct)
        assert after["orders"] == before["orders"] == 0
        assert after["ledger"] == before["ledger"]        # no reservation
        assert float((await L.cash_state(conn, acct))["reserved"]) == 0.0
        assert after["shadows"] == 1 and after["census"] == 1
        r = await conn.fetchrow(
            "SELECT * FROM paper_shadow_counterfactuals WHERE account_id=$1",
            acct)
        assert r["source"] == CA.SRC_LEDGER and r["strategy"] == EXPLORE
        assert r["order_key"] == o["idempotency_key"]
        assert r["pnl_class"] == "NOT_REALIZED_PNL"
        assert L._epoch(r["eligible_at"]) == o["eligible_at"]
    finally:
        await _done(conn, tr)


# ── §3 forward economics ─────────────────────────────────────────────

@pg
async def test_unknown_forward_economics_refuses_and_routes_to_shadow():
    from sportsassets.agents import paper_derek as PD
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "cauk", now=NOW - 60)
        acct = a["account_id"]
        assert (await LC.current_state(conn, acct, CG))["state"] == \
            LC.ACTIVE_CHALLENGER
        # the decision gate
        ctx = {"account_id": acct, "config": a["config"]}
        ce = await PD.capital_gate(
            conn, ctx, strategy=CG, p=0.60,
            levels=[{"price": 0.40, "qty": 1000}],
            sized={"qty": 300, "limit": 0.40}, cand=_cand("m-uk"),
            side="LONG", at=NOW, fee_fn=FEE, decision_id="paperdec:uk")
        assert ce["capital_eligible"] is False and ce["qty"] == 0
        assert ce["refusals"] == [CA.R_FORWARD_UNKNOWN]
        assert ce["capital_authority"]["forward_verdict"] == CA.UNKNOWN
        assert ce["shadow"]["recorded"] is True
        assert ce["total_executable_ev_usd"] > 0     # kept for the census
        # the ledger, for any strategy
        got = await L.submit_order(conn, _order(a, key="u1", strategy=CG),
                                   fee_fn=FEE, now=NOW)
        assert got["refusal"] == CA.R_FORWARD_UNKNOWN
        assert got["under_lock"] is True
        assert got["forward"]["verdict"] == CA.UNKNOWN
        c = await _counts(conn, acct)
        assert c["orders"] == 0 and c["shadows"] == 2
    finally:
        await _done(conn, tr)


@pg
async def test_negative_forward_economics_refuses_and_positive_permits():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "cang", now=NOW - 60)
        acct = a["account_id"]
        n = CA.MIN_FORWARD_OBSERVATIONS
        got = await _seed_shadows(conn, a, BENCH, n, outcome="LOST",
                                  payout=0.0)
        assert got["settled"] == n
        fe = await CA.forward_economics(conn, acct, BENCH, now=NOW)
        assert fe["ok"] and fe["verdict"] == CA.NEGATIVE
        assert fe["shadow"]["observations"] == n
        assert fe["realized_paper"]["observations"] == 0
        r = await L.submit_order(conn, _order(a, key="n1", strategy=BENCH),
                                 fee_fn=FEE, now=NOW)
        assert r["refusal"] == CA.R_FORWARD_NEGATIVE
        # another strategy with POSITIVE settled shadow evidence may enter
        got = await _seed_shadows(conn, a, CG, n, outcome="WON", payout=1.0)
        # ...and the refused BENCH entry's own ledger shadow settles too
        assert got["settled"] == n + 1
        assert (await CA.forward_economics(conn, acct, BENCH, now=NOW))[
            "verdict"] == CA.NEGATIVE
        ok = await L.submit_order(conn, _order(a, key="p1", strategy=CG),
                                  fee_fn=FEE, now=NOW)
        assert ok["ok"], ok
        ev = await conn.fetchval(
            "SELECT detail FROM paper_order_events WHERE order_id=$1 "
            "   AND kind='SUBMITTED'", ok["order"]["order_id"])
        ca = H.j(ev)["capital_authority"]
        assert ca["forward_verdict"] == CA.POSITIVE
        assert ca["forward_observations"] == n
        assert ca["total_executable_ev_usd"] > 0
        # SHADOW P&L NEVER BECOMES REALIZED PAPER P&L
        bal = await L.balances(conn, acct, now=NOW)
        assert bal["realized_pnl_usd"] == 0.0
        assert not [p for p in await L.positions(conn, acct,
                                                 include_closed=True)
                    if p["strategy"] in (CG, BENCH) and LC.closed_at(p)]
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
            " kind='SETTLEMENT'", acct) == 0
    finally:
        await _done(conn, tr)


@pg
async def test_shadow_settlement_uses_the_execution_book_or_the_limit_bound():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "cast", now=NOW - 60)
        acct = a["account_id"]
        rows = {}
        for key, book in (("nobook", None), ("book", [(0.40, 60)]),
                          ("away", [(0.45, 500)])):
            slug = "%s:%s" % (acct, key)
            ev = _evidence(p=0.60, levels=[{"price": 0.40, "qty": 100}],
                           qty=100, limit=0.40, slug=slug)
            got = await CA.record_shadow(
                conn, account_id=acct, strategy=CG, decision_id="d:" + key,
                order_key=None, source=CA.SRC_DECISION,
                capital_refusal=CA.R_FORWARD_UNKNOWN, lifecycle_state=None,
                slug=slug, holding_side="LONG", fixture="fx",
                payout_event="HOME", decided_at=NOW - 100, delay_s=2.0,
                expires_at=NOW - 10, simulator_version="t", evidence=ev)
            rows[key] = got["shadow_id"]
            if book:
                # observed BEFORE eligibility (ignored) and after it (used)
                await H.observe(conn, slug, NOW - 99.5,
                                offers=[(0.30, 1000)], bids=[(0.29, 10)])
                await H.observe(conn, slug, NOW - 97.0, offers=book,
                                bids=[(0.30, 10)])
        # still unsettled: nothing is written
        pend = await CA.settle_shadows(conn, now=NOW, account_id=acct,
                                       settlement_fn=_settled(None, None))
        assert pend["pending"] == 3 and pend["settled"] == 0
        got = await CA.settle_shadows(conn, now=NOW, account_id=acct,
                                      settlement_fn=_settled("WON", 1.0))
        assert got["settled"] == 3
        out = {k: await conn.fetchrow(
            "SELECT * FROM paper_shadow_counterfactual_outcomes "
            " WHERE shadow_id=$1", v) for k, v in rows.items()}
        assert out["nobook"]["execution_basis"] == CA.BASIS_LIMIT_BOUND
        assert float(out["nobook"]["filled_qty"]) == 100
        b = out["book"]
        assert b["execution_basis"] == CA.BASIS_EXEC_BOOK
        assert float(b["filled_qty"]) == 60              # partial depth
        fee60 = float(L._fee(None, 60, 0.40, NOW))
        assert float(b["counterfactual_pnl_usd"]) == pytest.approx(
            60 * 1.0 - 60 * 0.40 - fee60, abs=1e-5)
        assert out["away"]["outcome"] == "NO_FILL"
        assert float(out["away"]["counterfactual_pnl_usd"]) == 0.0
        for r in out.values():
            assert r["pnl_class"] == "NOT_REALIZED_PNL"
        # exactly once
        again = await CA.settle_shadows(conn, now=NOW, account_id=acct,
                                        settlement_fn=_settled("WON", 1.0))
        assert again["examined"] == 0
        # NO_FILL is not an economic observation
        assert len(await CA.shadow_pnls(conn, acct, CG)) == 2
    finally:
        await _done(conn, tr)


# ── §4 a stopping rule firing at the entry ───────────────────────────

@pg
async def test_a_stopping_rule_firing_now_refuses_though_the_state_permits(
        monkeypatch):
    from sportsassets import bettor_paper_simulator as SIM
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "carf", now=NOW - 7200)
        acct = a["account_id"]

        async def positive(conn, account_id, strategy, *, now,
                           positions=None):
            return dict(CA.forward_verdict([1.0] * 20, []), ok=True)
        monkeypatch.setattr(CA, "forward_economics", positive)
        for i in range(2):                          # 2 x $2,500 lost
            at = NOW - 3600 + i * 10
            o = _order(a, key="l%d" % i, strategy=CG, qty=5000, limit=0.50,
                       at=at)
            got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
            assert got["ok"], got
            await H.observe(conn, o["us_market_slug"], at + 3,
                            offers=[(0.50, 5000)], bids=[(0.48, 5000)])
            await SIM.simulate_order(conn, got["order"]["order_id"],
                                     now=at + 4, fee_fn=H.zero_fee)
            s = await L.settle(conn, account_id=acct, group_id=o["group_id"],
                               slug=o["us_market_slug"], holding_side="LONG",
                               settlement_event_key="ev-%d" % i,
                               outcome="LOST", evidence={"t": 1},
                               evidence_source="TEST", at=at + 60)
            assert s["ok"], s
        # the evaluator has NOT run: the stored state still permits entry
        assert (await LC.current_state(conn, acct, CG))["state"] == \
            LC.ACTIVE_CHALLENGER
        got = await L.submit_order(conn, _order(a, key="new", strategy=CG),
                                   fee_fn=H.zero_fee, now=NOW)
        assert got["refusal"] == CA.R_RULE_FIRING_AT_ENTRY, got
        fired = {r["rule_id"] for r in got["rules"]["firing"]}
        assert LC.RULE_LOSS_SHADOW in fired and LC.RULE_LOSS_REDUCE in fired
        # ...and at the decision gate, routed to shadow
        from sportsassets.agents import paper_derek as PD
        ce = await PD.capital_gate(
            conn, {"account_id": acct, "config": a["config"]}, strategy=CG,
            p=0.60, levels=[{"price": 0.40, "qty": 1000}],
            sized={"qty": 100, "limit": 0.40}, cand=_cand("m-rf"),
            side="LONG", at=NOW, fee_fn=FEE, decision_id="paperdec:rf")
        assert ce["refusals"] == [CA.R_RULE_FIRING_AT_ENTRY]
        assert ce["shadow"]["recorded"] is True
        # another strategy is unaffected
        ok = await L.submit_order(conn, _order(a, key="oth",
                                               strategy=BENCH),
                                  fee_fn=H.zero_fee, now=NOW)
        assert ok["ok"], ok
    finally:
        await _done(conn, tr)


# ── §5 executable EV for every strategy, explore and maker included ──

@pg
@pytest.mark.parametrize("strategy,order_type", [
    (EXPLORE, "MARKETABLE"), (MAKER, "RESTING"), (CG, "MARKETABLE")])
async def test_an_entry_without_positive_executable_ev_is_refused(
        strategy, order_type, monkeypatch):
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "caev", now=NOW - 60)
        acct = a["account_id"]

        async def positive(conn, account_id, strategy, *, now,
                           positions=None):
            return dict(CA.forward_verdict([1.0] * 20, []), ok=True)
        monkeypatch.setattr(CA, "forward_economics", positive)
        bare = _order(a, key="bare", strategy=strategy, evidence=False,
                      order_type=order_type)
        got = await L.submit_order(conn, bare, fee_fn=FEE, now=NOW)
        assert got["refusal"] == CA.R_EV_NOT_EVIDENCED
        neg = _order(a, key="neg", strategy=strategy, p=0.38,
                     order_type=order_type)
        assert neg["capital_evidence"]["total_executable_ev_usd"] <= 0
        got = await L.submit_order(conn, neg, fee_fn=FEE, now=NOW)
        assert got["refusal"] == CE.R_CE_CASH_WAIT_EV_NOT_POSITIVE
        # a forged "eligible" flag on non-positive EV is still refused
        forged = dict(neg, idempotency_key=neg["idempotency_key"] + ":f",
                      capital_evidence=dict(neg["capital_evidence"],
                                            capital_eligible=True,
                                            refusals=[]))
        got = await L.submit_order(conn, forged, fee_fn=FEE, now=NOW)
        assert got["refusal"] == CE.R_CE_CASH_WAIT_EV_NOT_POSITIVE
        assert (await _counts(conn, acct))["orders"] == 0
        # no shadow for an entry that fails EV (not a capital-authority miss)
        assert (await _counts(conn, acct))["shadows"] == 0
        good = _order(a, key="good", strategy=strategy, p=0.60,
                      order_type=order_type)
        ok = await L.submit_order(conn, good, fee_fn=FEE, now=NOW)
        assert ok["ok"], ok
    finally:
        await _done(conn, tr)


def test_explore_and_maker_evaluate_executable_ev_before_entering():
    for rel in ("agents/paper_explore.py", "agents/paper_maker.py"):
        src = (PKG / rel).read_text()
        assert "CA.evaluate_executable(" in src, rel
        assert '"capital_evidence": cevidence' in src, rel
        assert "CA.record_refusal(" in src, rel
    for rel in ("agents/paper_derek.py", "agents/paper_benchmark.py"):
        src = (PKG / rel).read_text()
        assert '"capital_evidence": cevidence' in src, rel
        assert "CA.record_refusal(" in src, rel


# ── §6 the instrumentation ───────────────────────────────────────────

@pg
async def test_every_refusal_row_carries_the_instrumentation():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "cain", now=NOW - 60)
        acct = a["account_id"]
        o = _order(a, key="i1", strategy=CG, qty=200, limit=0.40, p=0.55)
        o["label"] = dict(o["label"], line="-1.5", scope="FULL_GAME")
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["refusal"] == CA.R_FORWARD_UNKNOWN
        r = await conn.fetchrow(
            "SELECT * FROM paper_entry_refusal_census WHERE account_id=$1",
            acct)
        assert r["strategy"] == CG and r["stage"] == "LEDGER"
        assert r["refusal"] == CA.R_FORWARD_UNKNOWN
        assert r["us_market_slug"] == o["us_market_slug"]
        assert r["holding_side"] == "LONG"
        assert r["fixture"] == o["fixture"]
        assert (r["line"], r["scope"]) == ("-1.5", "FULL_GAME")
        assert float(r["gross_edge_pp"]) == pytest.approx(15.0)
        assert float(r["threshold_edge_pp"]) == 0.5
        assert float(r["edge_shortfall_pp"]) == 0.0
        assert float(r["expected_fees_usd"]) == pytest.approx(2.0)
        assert r["slippage_usd"] is not None
        assert r["adverse_selection_usd"] is not None
        assert float(r["total_executable_ev_usd"]) == pytest.approx(
            200 * 0.55 - 80.0 - 2.0)
        assert float(r["qty"]) == 200 and float(r["limit_price"]) == 0.40
        # a decision-stage refusal below the edge threshold: the shortfall
        rid = await CA.record_refusal(
            conn, account_id=acct, strategy=BENCH, stage="DECISION",
            refusal="BELOW_MIN_GROSS_EDGE", decision_id="d1",
            slug=o["us_market_slug"], holding_side="LONG", fixture="fx",
            p=0.402, best_price=0.40, threshold_edge_pp=0.5,
            expected_fees_usd=0.7, executable_ev_usd=-0.5, at=NOW)
        r2 = await conn.fetchrow(
            "SELECT * FROM paper_entry_refusal_census WHERE refusal_id=$1",
            rid)
        assert float(r2["gross_edge_pp"]) == pytest.approx(0.2)
        assert float(r2["edge_shortfall_pp"]) == pytest.approx(0.3)
        assert float(r2["expected_fees_usd"]) == 0.7
        assert float(r2["total_executable_ev_usd"]) == -0.5
        await CA.record_refusal(
            conn, account_id=acct, strategy=CG, stage="DECISION",
            refusal="BELOW_MIN_GROSS_EDGE", slug="other", holding_side="SHORT",
            fixture="fx2", at=NOW)
        census = await CA.blocker_census(conn, acct, since=NOW - 60)
        assert census["refused_entries"] == 3
        assert census["distinct_contract_sides"] == 2
        by = {b["refusal"]: b for b in census["by_refusal"]}
        assert by["BELOW_MIN_GROSS_EDGE"]["contract_sides"] == 2
        assert by["BELOW_MIN_GROSS_EDGE"]["opportunities"] == 2
        assert by[CA.R_FORWARD_UNKNOWN]["stages"] == ["LEDGER"]
        top = census["top_contract_sides_by_executable_ev"][0]
        assert top["us_market_slug"] == o["us_market_slug"]
        assert set(top["refusals"]) == {CA.R_FORWARD_UNKNOWN,
                                        "BELOW_MIN_GROSS_EDGE"}
    finally:
        await _done(conn, tr)


# ── §7 the acceptance read ───────────────────────────────────────────

@pg
async def test_the_acceptance_read_shape_and_assertions():
    from sportsassets.api import command_capital_authority as CCA
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "caac", now=NOW - 60)
        acct = a["account_id"]
        await _state(conn, acct, DEREK, LC.SHADOW_ONLY)
        await _state(conn, acct, EXPLORE, LC.SHADOW_ONLY)
        n = CA.MIN_FORWARD_OBSERVATIONS
        await _seed_shadows(conn, a, CG, n, outcome="WON", payout=1.0)
        ok = await L.submit_order(conn, _order(a, key="a1", strategy=CG),
                                  fee_fn=FEE, now=NOW)
        assert ok["ok"], ok
        no = await L.submit_order(conn, _order(a, key="a2", strategy=DEREK),
                                  fee_fn=FEE, now=NOW)
        assert no["refusal"] == LC.R_LIFECYCLE_SHADOW_ONLY
        uk = await L.submit_order(conn, _order(a, key="a3", strategy=BENCH),
                                  fee_fn=FEE, now=NOW)
        assert uk["refusal"] == CA.R_FORWARD_UNKNOWN
        got = await CCA.read(conn, account_id=acct, now=NOW + 1,
                             cutover=NOW - 3600)
        assert got["status"] == "OK", got
        assert got["authority"] == "PAPER_ONLY_NO_CAPITAL_AUTHORITY"
        d = got["data"]
        assert d["actual_profitability"] == "DEFERRED_FORWARD_EVIDENCE"
        assert d["cutover"] == {"at": NOW - 3600, "basis": "REQUESTED"}
        assert set(d["strategies"]) >= set(LC.KNOWN_STRATEGIES)
        s = d["strategies"]
        for row in s.values():
            for k in ("lifecycle_state", "paper_entry_allowed",
                      "paper_entry_refusal",
                      "new_paper_entries_since_cutover",
                      "assert_entries_in_no_entry_states",
                      "assert_entries_without_positive_forward_economics",
                      "shadow_forward", "realized_paper_pnl_usd",
                      "unrealized_paper_pnl_usd", "open_exposure_usd",
                      "settled_paper_forward_since_cutover",
                      "expected_executable_ev_at_decision_usd",
                      "forward_economics",
                      "promotion_evidence_shadow_forward"):
                assert k in row, k
            assert row["shadow_forward"]["evidence_class"] == \
                "SHADOW_COUNTERFACTUAL"
            assert row["shadow_forward"]["pnl_class"] == "NOT_REALIZED_PNL"
        assert s[DEREK]["lifecycle_state"] == LC.SHADOW_ONLY
        assert s[DEREK]["paper_entry_allowed"] is False
        assert s[DEREK]["paper_entry_refusal"] == LC.R_LIFECYCLE_SHADOW_ONLY
        assert s[DEREK]["new_paper_entries_since_cutover"] == 0
        assert s[BENCH]["paper_entry_allowed"] is False
        assert s[BENCH]["paper_entry_refusal"] == CA.R_FORWARD_UNKNOWN
        assert s[CG]["paper_entry_allowed"] is True
        assert s[CG]["new_paper_entries_since_cutover"] == 1
        assert s[CG]["forward_economics"]["verdict"] == CA.POSITIVE
        assert s[CG]["expected_executable_ev_at_decision_usd"][
            "paper_entries"] > 0
        sh = s[CG]["shadow_forward"]
        assert sh["settled"] == n and sh["counterfactual_pnl_usd"] > 0
        # shadow results are NOT realized PAPER P&L
        assert s[CG]["realized_paper_pnl_usd"] is None
        assert d["paper"]["includes_shadow"] is False
        assert d["paper"]["realized_pnl_usd"] == 0.0
        assert s[CG]["open_exposure_usd"] > 0           # the reservation
        # the promotion evidence is advisory only
        assert "never an automatic promotion" in s[CG][
            "promotion_evidence_shadow_forward"]["use"]
        assert (await LC.current_state(conn, acct, DEREK))["state"] == \
            LC.SHADOW_ONLY
        asr = d["assertions"]
        assert asr["zero_entries_in_shadow_only_quarantined_retired"] is True
        assert asr["zero_entries_with_unknown_or_negative_forward_economics"]
        assert asr["new_entries_since_cutover"] == 1
        census = d["blocker_census"]
        assert census["distinct_contract_sides"] == 2
        assert {b["refusal"] for b in census["by_refusal"]} == {
            LC.R_LIFECYCLE_SHADOW_ONLY, CA.R_FORWARD_UNKNOWN}
    finally:
        await _done(conn, tr)


@pg
async def test_an_entry_made_without_forward_economics_is_counted():
    """The assertion is computed from what each entry's SUBMITTED event
    recorded, so an entry admitted before (or around) the gate shows up."""
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "caav", now=NOW - 60)
        acct = a["account_id"]
        o = _order(a, key="old", strategy=CG)
        # an entry recorded before the capital authority: no verdict
        await conn.execute(
            "INSERT INTO paper_orders (order_id, idempotency_key, account_id,"
            " session_id, group_id, role, direction, holding_side, intent, "
            " us_market_slug, fixture, label, order_type, time_in_force, "
            " allow_partial, qty, limit_price, wire_price, decided_at, "
            " eligible_at, expires_at, simulator_version, reserved_usd, "
            " reserved_remaining_usd, state, strategy) VALUES ($1,$2,$3,$4,"
            " $5,'ENTRY','BUY','LONG',$6,$7,$8,'{}'::jsonb,'MARKETABLE','IOC',"
            " true,10,0.4,0.4,$9,$9,$9,'t',0,0,'EXPIRED',$10)",
            L.order_id_for(o["idempotency_key"]), o["idempotency_key"], acct,
            a["session_id"], o["group_id"], o["intent"],
            o["us_market_slug"], o["fixture"], L._ts(NOW - 60), CG)
        await _state(conn, acct, CG, LC.SHADOW_ONLY)
        v = await CA.acceptance_view(conn, account_id=acct, now=NOW,
                                     cutover=NOW - 3600)
        asr = v["assertions"]
        assert asr["entries_without_positive_forward_economics"] == 1
        assert asr[
            "zero_entries_with_unknown_or_negative_forward_economics"] is False
        # created while the state was still ACTIVE: not a no-entry violation
        assert asr["entries_in_no_entry_states"] == 0
    finally:
        await _done(conn, tr)


@pg
async def test_the_acceptance_read_is_unavailable_without_the_migration(
        monkeypatch):
    from sportsassets.api import command_capital_authority as CCA
    conn = await H.connect()
    try:
        async def no_schema(conn):
            return False
        monkeypatch.setattr(CA, "schema", no_schema)
        got = await CCA.read(conn, account_id="paper_t_none", now=NOW)
        assert got["status"] == "UNAVAILABLE" and got["data"] is None
    finally:
        await conn.close()


async def test_an_unreadable_acceptance_read_is_unavailable_never_zero(
        monkeypatch):
    from sportsassets.api import command_capital_authority as CCA

    async def pool():
        raise RuntimeError("db down")
    monkeypatch.setattr(CCA, "_pool", pool)
    CCA._CACHE.clear()
    got = await CCA.paper_capital_authority()
    assert got["status"] == "UNAVAILABLE" and got["data"] is None


def test_the_acceptance_route_is_get_only_and_needs_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_capital_authority as CCA
    assert CCA.PATH == "/api/command/paper/capital-authority"
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == CCA.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(CCA.PATH).status_code == 401
    for verb in (client.post, client.put, client.delete, client.patch):
        assert verb(CCA.PATH).status_code in (401, 405)


def test_the_acceptance_route_holds_no_write_and_no_order_path():
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+"
                       r"FROM|TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE)", re.I)
    src = (PKG / "api/command_capital_authority.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not write.search(node.value), node.value[:60]
    for word in ("submit_order", "place", "cancel", "record_shadow",
                 "record_refusal", "settle_shadows"):
        assert word not in src, word
    assert "readonly=True" in src


# ── §8 the tables, the imports, the registration ─────────────────────

@pg
async def test_the_tables_are_append_only_and_labelled():
    import asyncpg
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "caao", now=NOW - 60)
        await _seed_shadows(conn, a, CG, 1)
        sid = await conn.fetchval(
            "SELECT shadow_id FROM paper_shadow_counterfactuals "
            " WHERE account_id=$1", a["account_id"])
        await CA.record_refusal(conn, account_id=a["account_id"],
                                strategy=CG, stage="DECISION", refusal="X",
                                at=NOW)
        for sql in (
                "UPDATE paper_shadow_counterfactuals SET qty = 1",
                "DELETE FROM paper_shadow_counterfactuals",
                "TRUNCATE paper_shadow_counterfactuals CASCADE",
                "UPDATE paper_shadow_counterfactual_outcomes SET "
                " counterfactual_pnl_usd = 0",
                "DELETE FROM paper_shadow_counterfactual_outcomes",
                "UPDATE paper_entry_refusal_census SET refusal = 'Y'",
                "DELETE FROM paper_entry_refusal_census"):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(sql)
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO paper_shadow_counterfactual_outcomes "
                    " (shadow_id, outcome, execution_basis, filled_qty, "
                    "  exec_cost_usd, exec_fees_usd, counterfactual_pnl_usd,"
                    "  evidence, settled_at, pnl_class) VALUES ($1, 'WON', "
                    "  'x', 1, 0, 0, 1, '{}'::jsonb, now(), "
                    "  'REALIZED_PAPER_PNL')", sid)
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_shadow_counterfactuals WHERE "
            " evidence_class <> 'SHADOW_COUNTERFACTUAL' OR "
            " pnl_class <> 'NOT_REALIZED_PNL'") == 0
    finally:
        await _done(conn, tr)


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_the_module_reaches_no_funded_venue_or_order_execution_path():
    for rel in ("bettor_capital_authority.py",
                "api/command_capital_authority.py"):
        for m in _imports(PKG / rel):
            assert not any(k in (m or "") for k in (
                "funded", "entry_execution", "execution_gate", "pmus",
                "kalshi_venue", "live_executor", "execmirror")), (rel, m)
    src = (PKG / "bettor_capital_authority.py").read_text()
    assert "INSERT INTO paper_orders" not in src
    assert "INSERT INTO paper_ledger" not in src
    assert "_append(" not in src


def test_the_shadow_step_runs_in_the_paper_pass():
    from sportsassets.agents import paper_runtime as PR
    names = [n for n, _ in PR.default_steps()]
    assert "shadow_settlement" in names
    assert names.index("turnaround") < names.index("shadow_settlement")


def test_this_proof_is_registered_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_capital_authority.py" in listed.splitlines()
