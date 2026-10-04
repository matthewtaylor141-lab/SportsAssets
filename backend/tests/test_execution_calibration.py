"""CAPITAL-CRITICAL (R30C): LIVE EXECUTION CALIBRATION, THE THREE EVIDENCE
CLASSES KEPT APART.

  §1 THE RULE (pure). PAPER_SIMULATION, LIVE_SHADOW and ACTUAL are separate
     classes. A LIVE estimate uses ACTUAL when it is measured; otherwise it
     is labelled DERIVED_FROM_LIVE_SHADOW / DERIVED_FROM_PAPER_SIMULATION and
     its interval is WIDER than the class's own. ACTUAL with no venue fill
     is UNAVAILABLE with its reason -- never a zero. Intervals are
     event-clustered.
  §2 EVERY LIVE-PATH ESTIMATE STATES WHAT IT WAS FITTED ON: Eddie's estimate
     (and so the canonical intent's eddie component), the Opportunity Score
     V1's P(fill), Allie's executable net, the external-valuation lane's
     coverage, the twin's books and the micro-calibration lane -- each
     module's own declaration, pinned to execution_evidence.
  §3 THE READ MODEL over production-shaped rows written by the REAL writers
     (canonical intent, the PAPER adapter's simulated order, the SMALL LIVE
     SHADOW proposal, the books): the three classes side by side with n and
     intervals; the shadow proposal priced on the observed book; adverse
     selection at +30 s / +5 min; cancel and recovery; nothing pooled.
  §4 THE DATABASE (migration 233): a venue event can never name a SHADOW
     execution; the rollback refuses while tournament scores exist.
  §5 THE ROUTE is GET only, behind a command session, READ ONLY, and its
     modules hold no write and no order/venue/funded import.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import time
import uuid

import asyncpg
import pytest

from sportsassets import execution_calibration as EC
from sportsassets import execution_evidence as EE
from sportsassets.agents import eddie as E
from sportsassets.api import command_execution_calibration as XC

from tests import paper_harness as H
from tests import r30c_exec_fixture as F

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
MIG = ROOT / "migrations"
UP = (MIG / "233_execution_calibration_and_score_tournament.sql").read_text()
DOWN = (MIG / "rollback" /
        "233_execution_calibration_and_score_tournament.down.sql").read_text()


# ── §1 the rule (pure) ───────────────────────────────────────────────

def test_the_classes_are_three_and_actual_is_unmeasured_with_a_reason():
    assert EE.CLASSES == ("PAPER_SIMULATION", "LIVE_SHADOW", "ACTUAL")
    assert EE.LIVE_PRECEDENCE == ("ACTUAL", "LIVE_SHADOW", "PAPER_SIMULATION")
    assert EE.TRANSFER_PENALTY["ACTUAL"] == 1.0
    assert EE.TRANSFER_PENALTY["LIVE_SHADOW"] < \
        EE.TRANSFER_PENALTY["PAPER_SIMULATION"]
    assert "SHADOW" in EE.R_NO_ACTUAL and "small_live_order_events" in \
        EE.R_NO_ACTUAL
    for cls in (EE.PAPER_SIMULATION, EE.LIVE_SHADOW, EE.NO_FILL_EVIDENCE):
        assert "NOT_PROOF_OF_LIVE_EXECUTION" in EE.LIVE_USE[cls]
    p = EE.provenance(EE.PAPER_SIMULATION, basis="x", n=40)
    assert p["is_proof_of_live_execution"] is False
    assert p["actual"] == {"status": "UNMEASURED", "why": EE.R_NO_ACTUAL}
    with pytest.raises(ValueError):
        EE.provenance("SOMETHING_ELSE", basis="x")


def test_clustered_outcomes_never_buy_a_narrower_interval():
    ys = [1, 1, 1, 1, 0, 0, 0, 0] * 5
    solo = EE.clustered_proportion(ys)
    # the same outcomes, every event's four orders moving together
    cl = [i // 4 for i in range(len(ys))]
    tied = EE.clustered_proportion(ys, cl)
    assert solo["value"] == tied["value"] == 0.5
    assert tied["deff"] > 1.0 and tied["n_eff"] < solo["n_eff"]
    assert (tied["ci_high"] - tied["ci_low"]) > (solo["ci_high"]
                                                 - solo["ci_low"])
    assert tied["clusters"] == 10 and solo["clusters"] == 40
    m = EE.clustered_mean([0.01, 0.02, 0.03, -0.01], ["a", "a", "b", "c"])
    assert m["clusters"] == 3 and m["ci_low"] < m["value"] < m["ci_high"]
    assert EE.clustered_mean([0.01], ["a"])["why"] == \
        "FEWER_THAN_TWO_INDEPENDENT_EVENTS"
    assert EE.clustered_proportion([])["value"] is None
    assert EE.t_crit_95(1) == 12.706 and EE.t_crit_95(200) == \
        pytest.approx(1.972, abs=1e-3)


def _stat(value, n, lo, hi):
    return {"value": value, "n": n, "n_eff": n, "ci_low": lo, "ci_high": hi,
            "clusters": n}


def test_a_live_estimate_uses_actual_when_measured_else_is_labelled_and_wider():
    paper = _stat(0.9, 400, 0.87, 0.93)
    shadow = _stat(0.97, 200, 0.94, 0.99)
    actual = _stat(0.8, 25, 0.6, 0.92)
    got = EE.choose_live({EE.PAPER_SIMULATION: paper, EE.LIVE_SHADOW: shadow,
                          EE.ACTUAL: actual}, kind="proportion")
    assert got["fitted_on"] == EE.ACTUAL and got["is_proof_of_live_execution"]
    assert got["transfer_penalty"] == 1.0
    # no ACTUAL: derived from shadow, labelled, wider than its own interval
    got = EE.choose_live({EE.PAPER_SIMULATION: paper, EE.LIVE_SHADOW: shadow,
                          EE.ACTUAL: {"value": None, "n": 0,
                                      "why": EE.R_NO_ACTUAL}},
                         kind="proportion")
    assert got["fitted_on"] == EE.LIVE_SHADOW
    assert got["live_use"] == EE.LIVE_USE[EE.LIVE_SHADOW]
    assert got["is_proof_of_live_execution"] is False
    lo, hi = EE.wilson(0.97, 200)
    assert got["live_ci_low"] < lo and got["live_ci_high"] >= hi - 1e-9
    assert got["not_used"][EE.ACTUAL] == EE.R_NO_ACTUAL
    # shadow below the sample floor is never used: simulation, wider still
    got = EE.choose_live({EE.PAPER_SIMULATION: paper,
                          EE.LIVE_SHADOW: _stat(1.0, 5, 0.6, 1.0),
                          EE.ACTUAL: {}}, kind="proportion")
    assert got["fitted_on"] == EE.PAPER_SIMULATION
    assert "FEWER_THAN_20" in got["not_used"][EE.LIVE_SHADOW]
    lo, hi = EE.wilson(0.9, 400)
    assert got["live_ci_low"] < lo and got["live_ci_high"] > hi
    # a mean widens by sqrt(K)
    got = EE.choose_live({EE.PAPER_SIMULATION: _stat(0.01, 100, 0.0, 0.02)},
                         kind="mean")
    assert got["live_ci_low"] == pytest.approx(0.01 - 0.01 * 3)
    assert got["live_ci_high"] == pytest.approx(0.01 + 0.01 * 3)
    # nothing measured: UNAVAILABLE, never a zero
    got = EE.choose_live({}, kind="proportion")
    assert got["status"] == "UNAVAILABLE" and got["value"] is None


def test_summarise_never_reports_a_zero_for_an_unmeasured_class():
    out = EC.summarise(EE.ACTUAL, [], {}, unavailable_why=EE.R_NO_ACTUAL)
    assert out["status"] == "UNMEASURED" and out["orders"] == 0
    # an empty ACTUAL class proves nothing about live execution
    assert out["is_proof_of_live_execution"] is False
    for m in EC.METRICS:
        assert out["metrics"][m]["value"] is None, m
        assert out["metrics"][m]["status"] == "UNAVAILABLE", m
        assert out["metrics"][m]["why"] == EE.R_NO_ACTUAL, m


def _obs(i, *, full=True, cluster=None, opp=None, t=0.0, adv=0.01):
    return {"cls": EE.PAPER_SIMULATION, "intent_id": "cdi_%d" % i,
            "opportunity_id": opp or "opp_%d" % i,
            "cluster": cluster or "ev_%d" % i, "decided_at": t,
            "terminal": True, "requested_qty": 100.0,
            "filled_qty": 100.0 if full else 0.0, "full": full, "any": full,
            "vwap": 0.505 if full else None, "limit": 0.52,
            "best_at_decision": 0.50,
            "adverse": {30.0: adv, 300.0: 2 * adv} if full else {},
            "cancelled_remainder": not full}


def test_summarise_measures_each_metric_with_n_and_intervals():
    obs = [_obs(i, full=(i % 5 != 0), t=float(i)) for i in range(40)]
    EC.mark_recoveries(obs)
    out = EC.summarise(EE.PAPER_SIMULATION, obs, {"PAPER_REFUSED:x": 2})
    m = out["metrics"]
    assert out["orders"] == 40 and out["independent_events"] == 40
    assert m["fill_rate"]["value"] == pytest.approx(32 / 40)
    assert m["fill_rate"]["status"] == "MEASURED"
    assert m["fill_rate"]["ci_low"] < 0.8 < m["fill_rate"]["ci_high"]
    assert m["slippage_vs_limit_pp"]["value"] == pytest.approx(-0.015)
    assert m["slippage_vs_decision_best_pp"]["value"] == pytest.approx(0.005)
    assert m["adverse_selection_30s_pp"]["value"] == pytest.approx(0.01)
    assert m["adverse_selection_300s_pp"]["value"] == pytest.approx(0.02)
    assert m["cancel_rate"]["value"] == pytest.approx(8 / 40)
    # 8 cancelled, none of whose opportunity was retried: recovery measured 0
    # on n=8 -> shown, but below the floor (INSUFFICIENT_SAMPLE)
    assert m["recovery_rate"]["n"] == 8
    assert m["recovery_rate"]["status"] == "INSUFFICIENT_SAMPLE"
    assert out["excluded"] == {"PAPER_REFUSED:x": 2}
    live = EC.live_estimates({EE.PAPER_SIMULATION: out,
                              EE.ACTUAL: EC.summarise(
                                  EE.ACTUAL, [], {},
                                  unavailable_why=EE.R_NO_ACTUAL)})
    assert live["fill_rate"]["fitted_on"] == EE.PAPER_SIMULATION
    assert live["fill_rate"]["is_proof_of_live_execution"] is False
    assert live["fill_rate"]["not_used"][EE.ACTUAL] == EE.R_NO_ACTUAL


def test_a_later_fill_of_the_same_opportunity_is_a_recovery():
    a = _obs(1, full=False, opp="opp_x", t=0.0)
    b = _obs(2, full=True, opp="opp_x", t=30.0)
    c = _obs(3, full=False, opp="opp_y", t=0.0)
    d = _obs(4, full=True, opp="opp_y", t=EC.RECOVERY_WINDOW_S + 5)
    obs = [a, b, c, d]
    EC.mark_recoveries(obs)
    assert a["recovered"] is True and c["recovered"] is False
    assert b["recovered"] is None


# ── §2 every LIVE-path estimate states what it was fitted on ─────────

def _book(offers, bids, at, obs_id=9):
    return {"obs_id": obs_id, "observed_at": at, "tick": None,
            "market_state": None, "offers": H.md(offers=offers)["offers"],
            "bids": H.md(bids=bids)["bids"]}


def test_eddies_estimate_carries_its_evidence_class_and_a_wider_live_interval():
    T = 1_791_000_000.0
    h = F.eddie_history(40)
    assert h["evidence_class"] == EE.PAPER_SIMULATION
    assert h["adverse_selection"][E.TAKER]["sd_pp"] is not None
    cand = {"decision_id": "paper_d_x", "decided_at": T,
            "us_market_slug": "m", "holding_side": "LONG",
            "proposed_qty": 1000, "limit_price": 0.56, "p_blended": 0.62,
            "economics": {"acquisition": {"fees_usd": 10.0}}}
    est = E.estimate(cand, _book([(0.52, 5000)], [(0.50, 5000)], T - 5), h,
                     now=T + 1)
    ev = est["execution_evidence"]
    assert ev == est["inputs"]["execution_evidence"]       # persisted too
    assert ev["fitted_on"] == EE.PAPER_SIMULATION
    assert ev["is_proof_of_live_execution"] is False
    assert ev["actual"]["why"] == EE.R_NO_ACTUAL
    assert ev["fitted_on_by_input"]["fill_probability"] == EE.PAPER_SIMULATION
    assert ev["fitted_on_by_input"]["slippage"] == EE.NO_FILL_EVIDENCE
    fp = ev["fill_probability"]
    assert fp["value"] == pytest.approx(est["expected_fill_probability"])
    assert fp["n"] == 40
    assert fp["live_ci_low"] < fp["class_ci_low"] <= fp["value"]
    assert fp["live_ci_high"] >= fp["class_ci_high"]
    mk = ev["adverse_selection_pp"]
    assert mk["live_ci_low"] < mk["class_ci_low"] < mk["class_ci_high"] < \
        mk["live_ci_high"]
    # the canonical intent's eddie component carries it
    from sportsassets import canonical_components as CC
    comp = CC.eddie_component(est)
    assert comp["execution_evidence"]["fitted_on"] == EE.PAPER_SIMULATION
    # an unmeasured history is labelled, not bounded by invention
    est2 = E.estimate(cand, _book([(0.52, 5000)], [(0.50, 5000)], T - 5),
                      E.summarise_history([], [], []), now=T + 1)
    assert est2["execution_evidence"]["fill_probability"]["value"] is None
    assert est2["execution_evidence"]["fill_probability"]["why"]


def test_every_declaration_is_pinned_to_the_evidence_classes():
    from sportsassets import bettor_entry_execution as EX
    from sportsassets import calibration_execute as CEX
    from sportsassets import calibration_store as CST
    from sportsassets.lost_opportunity import score as SC
    from sportsassets.twin import common as TC
    assert SC.EXECUTION_EVIDENCE_CLASS == EE.PAPER_SIMULATION
    assert SC.EXECUTION_EVIDENCE_LIVE_USE == EE.LIVE_USE[EE.PAPER_SIMULATION]
    assert EX.EXECUTION_EVIDENCE_CLASS == EE.NO_FILL_EVIDENCE
    assert EX.EXECUTION_EVIDENCE["live_use"] == EE.LIVE_USE[EE.NO_FILL_EVIDENCE]
    assert EX.EXECUTION_EVIDENCE["is_proof_of_live_execution"] is False
    assert CST.EXECUTION_EVIDENCE_CLASS == EE.ACTUAL
    assert CEX.EXECUTION_EVIDENCE_CLASS == EE.ACTUAL
    assert E.EXECUTION_EVIDENCE_CLASS == EE.PAPER_SIMULATION
    assert TC.EXECUTION_EVIDENCE_CLASS == EC.TWIN_EXECUTION_EVIDENCE_CLASS
    assert TC.ACTUAL_BOOK_PATH == EC.TWIN_ACTUAL_BOOK_PATH
    rows = EC.estimates_in_use()
    mods = " ".join(r["module"] for r in rows)
    for m in ("agents/eddie.py", "lost_opportunity/score.py",
              "opportunity_score_v2.py", "allie_capital", "twin/common.py",
              "bettor_entry_execution.py", "calibration_store.py",
              "calibration_execute.py"):
        assert m in mods, m
    for r in rows:
        assert r["proof_of_live_execution"] is False, r
        assert r["actual_on_the_canonical_path"]["why"] == EE.R_NO_ACTUAL
        vals = (list(r["fitted_on"].values()) if isinstance(
            r["fitted_on"], dict) else [r["fitted_on"]])
        assert all(v in EE.FITTED_ON_VALUES for v in vals), r


def test_the_v1_score_and_the_entry_lane_label_their_execution_inputs():
    from sportsassets import bettor_entry_execution as EX
    from sportsassets.lost_opportunity import score as SC
    got = SC.score({"status": "MEASURED", "executable_opportunity_dollars": 10,
                    "executable_capacity_usd": 100, "decided_at": 0.0,
                    "event_start_at": 3600.0}, fill_probability=0.8,
                   fill_basis="b", fill_source=SC.EDDIE,
                   idle_capital_usd=1e6,
                   lag_samples=[(-10.0 - i, 3600.0) for i in range(6)])
    ex = got["components"]["EXECUTION_CONFIDENCE"]
    assert ex["evidence_class"] == EE.PAPER_SIMULATION
    assert "NOT_PROOF_OF_LIVE_EXECUTION" in ex["live_use"]
    none = SC.score({"status": "MEASURED", "executable_opportunity_dollars": 10,
                     "executable_capacity_usd": 100})
    assert none["components"]["EXECUTION_CONFIDENCE"]["evidence_class"] is None
    out = EX.estimate(ladder=None, fair_value=None, fee_fn=None)
    assert out["ok"] is False
    assert out["execution_evidence"]["fitted_on"] == EE.NO_FILL_EVIDENCE


def test_the_shadow_proposal_is_walked_by_the_simulators_own_walk():
    md = H.md(offers=[(0.50, 1), (0.51, 5)], bids=[(0.48, 10)])
    got = XC.shadow_fill(md, holding_side="LONG", qty=3, limit=0.52,
                         time_in_force="IOC")
    assert got["filled_qty"] == 3 and got["best"] == pytest.approx(0.50)
    assert got["vwap"] == pytest.approx((0.50 + 2 * 0.51) / 3)
    beyond = XC.shadow_fill(md, holding_side="LONG", qty=3, limit=0.49,
                            time_in_force="IOC")
    assert beyond["filled_qty"] == 0 and beyond["vwap"] is None
    fok = XC.shadow_fill(md, holding_side="LONG", qty=50, limit=0.52,
                         time_in_force="FOK")
    assert fok["filled_qty"] == 0                 # all or none
    assert XC.side_mid(md, "LONG") == pytest.approx(0.49)
    assert XC.shadow_fill(None, holding_side="LONG", qty=1, limit=0.5,
                          time_in_force="IOC")["why"] == \
        "DECISION_BOOK_UNREADABLE"


# ── §3 the read model over rows the real writers wrote ───────────────

@pg
async def test_three_classes_side_by_side_over_real_writer_rows():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await F.prepare(conn)
        acct = await H.new_account(conn, "xcal")
        T = time.time()
        tag = uuid.uuid4().hex[:8]
        a = await F.decision(conn, acct, T=T, slug="test-xcal-a-%s" % tag,
                             event="ev-a-%s" % tag,
                             offers=[(0.50, 1500), (0.52, 1000)],
                             bids=[(0.48, 2000)])
        r = await F.fill(conn, a, at=T + 4, offers=[(0.50, 1500),
                                                    (0.52, 1000)],
                         bids=[(0.48, 2000)])
        assert r["state"] == "FILLED", r
        # an opportunity whose book is beyond the limit, then re-evaluated
        b = await F.decision(conn, acct, T=T + 5, slug="test-xcal-b-%s" % tag,
                             event="ev-b-%s" % tag, offers=[(0.60, 3000)],
                             bids=[(0.58, 3000)])
        r = await F.fill(conn, b, at=T + 9, offers=[(0.60, 3000)],
                         bids=[(0.58, 3000)])
        assert r["state"] in ("EXPIRED", "CANCELED"), r
        c = await F.decision(conn, acct, T=T + 20,
                             slug="test-xcal-b-%s" % tag,
                             event="ev-b-%s" % tag,
                             offers=[(0.50, 3000)], bids=[(0.48, 3000)])
        r = await F.fill(conn, c, at=T + 24, offers=[(0.50, 3000)],
                         bids=[(0.48, 3000)])
        assert r["state"] == "FILLED", r
        # the books 30 s and 5 min later: the mid fell (adverse to a buyer)
        for slug in ("test-xcal-a-%s" % tag, "test-xcal-b-%s" % tag):
            await H.observe(conn, slug, T + 40, bids=[(0.46, 2000)],
                            offers=[(0.48, 2000)])
            await H.observe(conn, slug, T + 315, bids=[(0.44, 2000)],
                            offers=[(0.46, 2000)])
        mine = {a["intent"]["intent_id"], b["intent"]["intent_id"],
                c["intent"]["intent_id"]}
        p_obs, p_ex = await XC.paper_observations(conn, since=T - 2,
                                                  until=T + 30)
        s_obs, s_ex = await XC.shadow_observations(conn, since=T - 2,
                                                   until=T + 30)
        a_obs, _a_ex, integrity = await XC.actual_observations(
            conn, since=T - 2, until=T + 1000)
        p = {o["intent_id"]: o for o in p_obs if o["intent_id"] in mine}
        s = {o["intent_id"]: o for o in s_obs if o["intent_id"] in mine}
        assert set(p) == set(s) == mine
        assert integrity == 0 and not [o for o in a_obs
                                       if o["intent_id"] in mine]
        ia, ib, ic = (a["intent"]["intent_id"], b["intent"]["intent_id"],
                      c["intent"]["intent_id"])
        # PAPER_SIMULATION: the simulator walked 2000 through two levels
        assert p[ia]["full"] and p[ia]["filled_qty"] == 2000
        assert p[ia]["vwap"] == pytest.approx((1500 * 0.50 + 500 * 0.52)
                                              / 2000)
        assert p[ia]["limit"] == pytest.approx(0.52)
        assert p[ia]["best_at_decision"] == pytest.approx(0.50)
        # mid at the fill book .49 -> .47 at +30 s -> .45 at +5 min
        assert p[ia]["adverse"][30.0] == pytest.approx(0.02)
        assert p[ia]["adverse"][300.0] == pytest.approx(0.04)
        assert p[ib]["any"] is False and p[ib]["cancelled_remainder"]
        assert p[ib]["recovered"] is True            # c filled the same opp
        assert p[ic]["opportunity_id"] == p[ib]["opportunity_id"]
        # LIVE_SHADOW: the live-size order (2000 / 1000) on the decision book
        assert s[ia]["requested_qty"] == 2 and s[ia]["full"]
        assert s[ia]["vwap"] == pytest.approx(0.50)
        assert s[ia]["adverse"][30.0] == pytest.approx(0.02)
        assert s[ib]["filled_qty"] == 0 and s[ib]["cancelled_remainder"]
        assert s[ib]["recovered"] is True
        # the classes summarised apart, the ACTUAL class unmeasured by name
        own_p = EC.summarise(EE.PAPER_SIMULATION, list(p.values()), p_ex)
        own_s = EC.summarise(EE.LIVE_SHADOW, list(s.values()), s_ex)
        own_a = EC.summarise(EE.ACTUAL, [], {},
                             unavailable_why=EE.R_NO_ACTUAL)
        assert own_p["metrics"]["fill_rate"]["value"] == pytest.approx(2 / 3)
        assert own_p["metrics"]["fill_rate"]["n"] == 3
        assert own_p["metrics"]["fill_rate"]["clusters"] == 2   # 2 events
        assert own_p["metrics"]["fill_rate"]["status"] == \
            "INSUFFICIENT_SAMPLE"
        assert own_s["metrics"]["slippage_vs_limit_pp"]["value"] == \
            pytest.approx(-0.02)
        assert own_a["metrics"]["fill_rate"]["why"] == EE.R_NO_ACTUAL
        live = EC.live_estimates({EE.PAPER_SIMULATION: own_p,
                                  EE.LIVE_SHADOW: own_s, EE.ACTUAL: own_a})
        # three orders are below every class's floor: no LIVE estimate is
        # claimed from them, and each class says why
        assert live["fill_rate"]["status"] == "UNAVAILABLE"
        assert live["fill_rate"]["not_used"][EE.ACTUAL] == EE.R_NO_ACTUAL
        # the whole report (bounded reads, READ ONLY in the route)
        rep = await XC.report(conn, since=T - 2, until=T + 30, now=T + 400)
        assert set(rep["classes"]) == set(EE.CLASSES)
        assert rep["pooled_across_classes"] is False
        assert rep["classes"]["ACTUAL"]["status"] == "UNMEASURED"
        assert rep["classes"]["ACTUAL"]["integrity_violations"] == 0
        assert rep["classes"]["LIVE_SHADOW"]["is_proof_of_live_execution"] \
            is False
        assert rep["rule_sha"] == EE.RULE_SHA
        assert rep["eddie_fit"]["status"] == "OK"
        assert rep["eddie_fit"]["fitted_on"] == EE.PAPER_SIMULATION
        # nothing reached a venue
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0
    finally:
        await tx.rollback()
        await conn.close()


# ── §4 the database ──────────────────────────────────────────────────

async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


@pg
async def test_a_venue_event_can_never_name_a_shadow_execution():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                         # idempotent
        await F.prepare(conn)
        acct = await H.new_account(conn, "xcalv")
        tag = uuid.uuid4().hex[:8]
        d = await F.decision(conn, acct, T=time.time(),
                             slug="test-xcalv-%s" % tag, event="ev-%s" % tag,
                             offers=[(0.50, 3000)], bids=[(0.48, 3000)])
        eid = await conn.fetchval(
            "SELECT execution_id FROM canonical_intent_executions WHERE "
            " intent_id = $1 AND adapter = 'SMALL_LIVE'",
            d["intent"]["intent_id"])
        assert eid
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO small_live_order_events (execution_id,
                           venue_order_id, state, cum_qty, source,
                           venue_record) VALUES ($1, 'v-1', 'FILLED', 2,
                           'VENUE_ORDER_RECORD', '{}')""", eid)
        peid = await conn.fetchval(
            "SELECT execution_id FROM canonical_intent_executions WHERE "
            " intent_id = $1 AND adapter = 'PAPER'",
            d["intent"]["intent_id"])
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO small_live_order_events (execution_id,
                           venue_order_id, state, cum_qty, source,
                           venue_record) VALUES ($1, 'v-2', 'FILLED', 2,
                           'VENUE_ORDER_RECORD', '{}')""", peid)
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_233_rollback_refuses_over_scores_and_drops_only_its_objects():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await F.prepare(conn)
        acct = await H.new_account(conn, "xcalr")
        tag = uuid.uuid4().hex[:8]
        d = await F.decision(conn, acct, T=time.time(),
                             slug="test-xcalr-%s" % tag, event="ev-%s" % tag,
                             offers=[(0.50, 3000)], bids=[(0.48, 3000)])
        assert d["tournament_recorded"] is True
        await _expect(conn, asyncpg.exceptions.RaiseError, DOWN)
        drops = [ln for ln in DOWN.splitlines() if ln.startswith("DROP ")]
        assert drops and all(
            "opportunity" in ln or "small_live_order_event_live_only" in ln
            for ln in drops), drops
    finally:
        await tx.rollback()
        await conn.close()
    # with no score, the rollback drops 233's objects cleanly (applied into
    # a scratch schema first on the search path, inside a rolled-back
    # transaction, so the shared table and its rows are untouched)
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("CREATE SCHEMA r30c_rollback_probe")
        await conn.execute(
            "SET LOCAL search_path = r30c_rollback_probe, public")
        await conn.execute(UP)
        assert await conn.fetchval(
            "SELECT to_regclass('r30c_rollback_probe."
            "opportunity_score_tournament') IS NOT NULL")
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('r30c_rollback_probe."
            "opportunity_score_tournament') IS NULL")
        assert await conn.fetchval(
            "SELECT to_regclass('public.canonical_decision_intents') "
            "IS NOT NULL")
        assert await conn.fetchval(
            "SELECT to_regclass('public.small_live_order_events') IS NOT NULL")
        await conn.execute(UP)                 # and it re-applies
    finally:
        await tx.rollback()
        await conn.close()


# ── §5 the route and the modules ─────────────────────────────────────

WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE)\b",
                   re.I)
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution_intent", "execution_gate", "bettor_funded", "submit",
             "live_parity", "live_executor", "smalllive")


def _imports(path) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_the_modules_hold_no_write_and_no_order_or_venue_import():
    for rel in ("api/command_execution_calibration.py",
                "execution_calibration.py", "execution_evidence.py"):
        for node in ast.walk(ast.parse((PKG / rel).read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not WRITE.search(node.value), (rel, node.value[:60])
        for imp in _imports(PKG / rel):
            leaf = imp.rsplit(".", 1)[-1]
            assert not any(f in leaf for f in FORBIDDEN), (rel, imp)
    # the evidence rule is pure: standard library only
    assert _imports(PKG / "execution_evidence.py") <= {
        "__future__", "annotations", "hashlib", "json", "math"}


def test_the_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == XC.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(XC.PATH).status_code == 401
    assert client.post(XC.PATH).status_code in (401, 405)
    src = (PKG / "api" / "command_execution_calibration.py").read_text()
    assert "readonly=not nested" in src and "statement_timeout" in src
    assert json.dumps(EE.RULE)                    # serializable as shown
