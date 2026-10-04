"""CAPITAL-CRITICAL (R30C): THE OPPORTUNITY SCORE V1 / V2 SHADOW TOURNAMENT.

  §1 V2 (pure): an uncertainty-adjusted LOWER-CONFIDENCE-BOUND score of
     executable EV (after spread, depth, fees, slippage, adverse selection),
     the fill probability's lower bound (widened by the execution-evidence
     class it was fitted on), Allie's capital-hours, the fixture correlation
     haircut and tail risk (exceptional settlement upper bound, variance
     cost). Every missing input is UNAVAILABLE with its reason, never 0; the
     spec is frozen and hashed.
  §2 THE TOURNAMENT (pure): unique opportunities (re-evaluations counted,
     never pooled as new evidence), INVESTMENT only, forward only (no
     cutover -> NOT_ESTABLISHED), rank correlation / top-k / calibration
     with EVENT-CLUSTER bootstrap intervals, and a pre-declared promotion
     rule whose best outcome is PENDING_OWNER_DECISION -- never automatic.
  §3 THE REAL WRITERS: the decision hook records V1 and V2 beside every
     canonical intent at its decision instant (the real paper pass); the
     outcome join reads the paper ledger's realized P&L of the intent's own
     paper order; the database refuses a backfilled or authority-bearing
     row and every UPDATE / DELETE / TRUNCATE.
  §4 NO AUTHORITY: V2 is never in the intent and nothing on the decision
     path reads the tournament; the route is GET only behind a command
     session, READ ONLY.
"""
from __future__ import annotations

import ast
import json
import math
import pathlib
import re
import time
import uuid

import asyncpg
import pytest

from sportsassets import execution_evidence as EE
from sportsassets import opportunity_score_v2 as V2
from sportsassets import opportunity_tournament as OT
from sportsassets.api import command_opportunity_tournament as CT

from tests import paper_harness as H
from tests import r30c_exec_fixture as F

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
TAKER = "TAKER_MARKETABLE"


# ── §1 V2 (pure) ─────────────────────────────────────────────────────

def _est(*, n=40, fill=0.9, mean=0.004, sd=0.002, p=0.62, vwap=0.52,
         qty=1000, fee=0.01, cls=EE.PAPER_SIMULATION):
    return {"estimate_id": "eex:t", "probability": p,
            "execution_style": TAKER,
            "inputs": {"planned_vwap": vwap, "planned_qty": qty,
                       "fee_pp_taker": fee, "history": {
                           "fill_rate": {TAKER: {
                               "value": fill, "numerator": round(fill * n),
                               "denominator": n}},
                           "adverse_selection": {TAKER: {
                               "value": max(0.0, mean), "raw_mean_pp": mean,
                               "sd_pp": sd, "n": n}},
                           "evidence_class": cls}}}


def _allie(hours=3.0, haircut=0.0, idle=400_000.0):
    return {"expected_hours_to_capital_release": hours,
            "correlation_concentration": {"haircut": haircut},
            "opportunity_cost": {"idle_capital_usd": idle}}


EX = {"rate_ucb": 0.05, "k": 0, "n": 60, "scope": "baseball", "basis": "t"}


def _v2(**kw):
    est = kw.pop("est", None) or _est()
    allie = kw.pop("allie", None) or _allie()
    ex = kw.pop("ex", EX)
    return V2.score(V2.inputs_from(est, allie, ex))


def test_v2_is_the_lower_bound_formula_it_declares():
    got = _v2()
    assert got["status"] == "MEASURED" and got["spec_sha"] == V2.SPEC_SHA
    k = EE.TRANSFER_PENALTY[EE.PAPER_SIMULATION]
    f_lcb, _ = EE.wilson(0.9, 40 / k)
    a_ucb = 0.004 + EE.t_crit_95(39) * 0.002 * math.sqrt(k) / math.sqrt(40)
    e = 0.62 - 0.52 - 0.01 - a_ucb
    ev = f_lcb * e * 1000 * (1 - 0.0) * (1 - 0.05)
    tail = f_lcb * 1000 ** 2 * 0.62 * 0.38 / (2 * 400_000)
    capital = 1000 * (0.52 + 0.01)
    assert got["predicted_net_lcb_usd"] == pytest.approx(ev - tail, rel=1e-6)
    assert got["opportunity_score"] == pytest.approx(
        (ev - tail) / (capital * 3.0), rel=1e-6)
    assert got["unit"] == "USD_LCB_NET_PER_USD_CAPITAL_HOUR"
    assert got["authority"] == "SHADOW_NO_AUTHORITY"
    c = got["components"]
    assert c["fill_probability"]["lcb"] < c["fill_probability"]["point"]
    assert c["fill_probability"]["fitted_on"] == EE.PAPER_SIMULATION
    assert c["adverse_selection_pp"]["ucb"] > c["adverse_selection_pp"]["mean"]
    assert got["execution_evidence"]["actual"]["why"] == EE.R_NO_ACTUAL


def test_v2_rewards_evidence_and_penalizes_correlation_and_tail():
    base = _v2()["opportunity_score"]
    # more fill / markout evidence: a tighter bound, a higher score
    assert _v2(est=_est(n=400))["opportunity_score"] > base
    # the same rates measured on ACTUAL fills are not widened
    assert _v2(est=_est(cls=EE.ACTUAL))["opportunity_score"] > base
    assert _v2(est=_est(cls=EE.LIVE_SHADOW))["opportunity_score"] > base
    # correlation with the open book on the fixture
    assert _v2(allie=_allie(haircut=0.5))["opportunity_score"] < base
    # a wider exceptional-settlement bound
    assert _v2(ex=dict(EX, rate_ucb=0.5))["opportunity_score"] < base
    # less idle capital: a bigger variance cost against it
    assert _v2(allie=_allie(idle=5_000.0))["opportunity_score"] < base
    # a longer hold: more capital-hours
    assert _v2(allie=_allie(hours=30.0))["opportunity_score"] < base


def test_v2_says_why_instead_of_scoring_zero():
    for kw, key in (
            ({"est": {"status": "UNAVAILABLE", "why": "COMPONENT_TIMEOUT"}},
             "execution"),
            ({"est": _est(n=0)}, "fill_probability"),
            ({"est": _est(sd=None)}, "adverse_selection"),
            ({"allie": {"correlation_concentration": {"haircut": 0.0},
                        "opportunity_cost": {"idle_capital_usd": 1e5},
                        "unmeasured": {"expected_hours_to_capital_release":
                                       "NO_EVENT_START"}}},
             "hours_to_capital_release"),
            ({"allie": {"expected_hours_to_capital_release": 3.0,
                        "opportunity_cost": {"idle_capital_usd": 1e5}}},
             "correlation"),
            ({"ex": None}, "exceptional_settlement")):
        got = _v2(**kw)
        assert got["status"] == "UNAVAILABLE", kw
        assert got["opportunity_score"] is None, kw
        assert key in got["unmeasured"] and got["why"], (kw, got["why"])
    # a negative lower bound is information: measured, negative, and the
    # haircuts never make it look better
    neg = _v2(est=_est(p=0.50))
    assert neg["status"] == "MEASURED" and neg["opportunity_score"] < 0
    worse = _v2(est=_est(p=0.50), allie=_allie(haircut=0.75))
    assert worse["opportunity_score"] == pytest.approx(
        neg["opportunity_score"])
    zero = _v2(allie=_allie(idle=0.0))
    assert zero["opportunity_score"] == 0.0
    assert zero["score_basis"] == "MEASURED_ZERO_NO_IDLE_CAPITAL"


def test_the_exceptional_settlement_bound_uses_the_sport_else_pooled_else_none():
    own = V2.exceptional_from_counts(scope_counts={"nfl": {"k": 1, "n": 50}},
                                     scope_key="nfl",
                                     pooled={"k": 0, "n": 500})
    assert own["scope"] == "nfl" and own["rate_ucb"] > 1 / 50
    pooled = V2.exceptional_from_counts(scope_counts={}, scope_key="nfl",
                                        pooled={"k": 2, "n": 400})
    assert pooled["scope"] == "POOLED_ALL_PAPER_SETTLEMENTS"
    none = V2.exceptional_from_counts(scope_counts={}, scope_key="nfl",
                                      pooled={"k": 0, "n": 0})
    assert none["rate_ucb"] is None and "unknown is not zero" in none["why"]
    # few settled markets: a wide bound, never a zero rate
    few = V2.exceptional_from_counts(scope_counts={"x": {"k": 0, "n": 3}},
                                     scope_key="x", pooled=None)
    assert few["rate_ucb"] > 0.4


def test_the_v2_spec_is_frozen_and_hashed():
    assert V2.SPEC_SHA == __import__("hashlib").sha256(json.dumps(
        V2.SPEC, sort_keys=True).encode()).hexdigest()
    assert V2.SPEC["transfer_penalty"] == EE.TRANSFER_PENALTY
    assert V2.SPEC["authority"] == "SHADOW_NO_AUTHORITY"


# ── §2 the tournament (pure) ─────────────────────────────────────────

def _intent(slug="m-1", side="LONG", event="ev-1", **extra):
    it = {"intent_id": "cdi_" + "0" * 24, "intent_version": "V1",
          "content_sha": "a" * 64, "decision_id": "d",
          "strategy": "PINNACLE_COMPLETED_GAME_PAPER", "sleeve": "INVESTMENT",
          "us_market_slug": slug, "holding_side": side,
          "contract": {"event_key": event}, "created_at": 1.0}
    it.update(extra)
    return it


def test_the_opportunity_key_is_event_market_side_line_scope():
    a = OT.opportunity_key(_intent())
    b = OT.opportunity_key(_intent())
    assert a == b and a["version"] == OT.KEY_VERSION
    assert a["opportunity_id"].startswith("opp_") and a["event_key"] == "ev-1"
    assert OT.opportunity_key(_intent(side="SHORT"))["opportunity_id"] != \
        a["opportunity_id"]
    assert OT.opportunity_key(_intent(slug="m-2"))["opportunity_id"] != \
        a["opportunity_id"]
    assert OT.opportunity_key(_intent(sleeve="TRAINING"))["opportunity_id"] \
        != a["opportunity_id"]
    own = OT.opportunity_key(_intent(opportunity_id="uop_42",
                                     opportunity_key_version="UOP_V1"))
    assert own["opportunity_id"] == "uop_42" and own["version"] == "UOP_V1"


def test_the_outcome_of_an_intent_is_the_ledger_or_a_named_exclusion():
    assert OT.intent_outcome(None, [])["why"] == "NO_PAPER_ORDER"
    assert OT.intent_outcome({"state": "RESTING", "filled_qty": 0},
                             [])["why"] == "ORDER_NOT_TERMINAL"
    z = OT.intent_outcome({"state": "EXPIRED", "filled_qty": 0}, [])
    assert z["state"] == "RESOLVED" and z["realized_net_usd"] == 0.0
    assert z["basis"] == "TERMINAL_ORDER_NEVER_FILLED_MEASURED_ZERO"
    assert OT.intent_outcome({"state": "FILLED", "filled_qty": 5},
                             [{"open_qty": 5, "realized_pnl_usd": 0}]
                             )["why"] == "POSITION_OPEN"
    r = OT.intent_outcome({"state": "FILLED", "filled_qty": 5},
                          [{"open_qty": 0, "realized_pnl_usd": 2.5},
                           {"open_qty": 0, "realized_pnl_usd": -1.0}])
    assert r["state"] == "RESOLVED" and r["realized_net_usd"] == 1.5


def _entry(i, *, opp, event, v1, v2, at, sleeve="INVESTMENT", v1p=None,
           v2p=None):
    return {"intent_id": "cdi_%024d" % i, "opportunity_id": opp,
            "event_key": event, "decided_at": at, "sleeve": sleeve,
            "v1_status": "MEASURED" if v1 is not None else "UNAVAILABLE",
            "v1_score": v1, "v1_why": None if v1 is not None else "X: y",
            "v2_status": "MEASURED" if v2 is not None else "UNAVAILABLE",
            "v2_score": v2, "v2_why": None if v2 is not None else "Z: w",
            "v1_predicted_net_usd": v1p, "v2_predicted_net_lcb_usd": v2p}


def _world(n_events, *, per_event=1, v2_good=True):
    entries, outcomes, i = [], {}, 0
    for ev in range(n_events):
        for k in range(per_event):
            i += 1
            quality = (ev * 7 % 11) - 5 + 0.1 * k
            v2 = quality if v2_good else -quality
            e = _entry(i, opp="opp_%d_%d" % (ev, k), event="ev_%d" % ev,
                       v1=-quality + 0.01 * k, v2=v2, at=100.0 + i,
                       v1p=-quality, v2p=quality)
            entries.append(e)
            outcomes[e["intent_id"]] = {"state": "RESOLVED",
                                        "realized_net_usd": 10.0 * quality}
    return entries, outcomes


def test_no_cutover_means_no_forward_window_and_no_conclusion():
    entries, outcomes = _world(40)
    got = OT.compute(entries, outcomes, since=None, cutover=None)
    assert got["status"] == "NOT_ESTABLISHED"
    assert got["why"] == OT.R_NO_CUTOVER
    assert got["enter_rule_changed"] is False


def test_rank_correlation_top_k_and_calibration_with_cluster_intervals():
    entries, outcomes = _world(40)
    got = OT.compute(entries, outcomes, since=0.0, cutover=0.0)
    assert got["status"] == "OK"
    v1, v2 = got["common"]["V1"], got["common"]["V2"]
    assert v1["n_opportunities"] == v2["n_opportunities"] == 40
    assert v2["n_independent_events"] == 40
    assert v2["rank_correlation"]["value"] > 0.9
    assert v1["rank_correlation"]["value"] < -0.9
    assert v2["rank_correlation"]["ci_low"] <= v2["rank_correlation"][
        "value"] <= v2["rank_correlation"]["ci_high"]
    assert v2["top_k_realized_net"]["value_mean_usd"] > \
        v1["top_k_realized_net"]["value_mean_usd"]
    assert v2["top_k_realized_net"]["k"] == 8
    assert v2["calibration"]["slope_realized_on_predicted"] == \
        pytest.approx(10.0)
    assert len(v2["calibration"]["buckets"]) == 5
    pair = got["paired_v2_minus_v1"]
    assert pair["rank_correlation_diff"]["ci_low"] > 0
    assert pair["top_k_mean_diff_usd"]["ci_low"] > 0
    pe = got["promotion_evidence"]
    assert pe["status"] == "EVIDENCE_SUPPORTS_V2_PENDING_OWNER_DECISION"
    assert pe["automatic_promotion"] is False
    assert pe["authority_granted"] == "NONE"
    # the bootstrap is deterministic (seeded by the inputs)
    again = OT.compute(entries, outcomes, since=0.0, cutover=0.0)
    assert again["paired_v2_minus_v1"] == pair


def test_correlated_opportunities_are_one_event_and_too_few_events_conclude_nothing():
    entries, outcomes = _world(10, per_event=4)
    got = OT.compute(entries, outcomes, since=0.0, cutover=0.0)
    v2 = got["common"]["V2"]
    assert v2["n_opportunities"] == 40 and v2["n_independent_events"] == 10
    pe = got["promotion_evidence"]
    assert pe["status"] == "INSUFFICIENT_OUT_OF_SAMPLE_EVIDENCE"
    assert pe["checks"]["MIN_INDEPENDENT_EVENTS"] is False
    # V2 worse than V1: not shown better, whatever the sample
    entries, outcomes = _world(40, v2_good=False)
    bad = OT.compute(entries, outcomes, since=0.0, cutover=0.0)
    assert bad["promotion_evidence"]["status"] == "V2_NOT_SHOWN_BETTER"


def test_only_forward_investment_unique_opportunities_count():
    entries, outcomes = _world(5)
    # a re-evaluation of opportunity 0 (later, other scores): counted, not a
    # new observation; its outcome adds to the opportunity's
    re_ = _entry(900, opp="opp_0_0", event="ev_0", v1=99.0, v2=99.0,
                 at=500.0)
    outcomes[re_["intent_id"]] = {"state": "RESOLVED",
                                  "realized_net_usd": 1.0}
    training = _entry(901, opp="opp_t", event="ev_t", v1=1.0, v2=1.0,
                      at=500.0, sleeve="TRAINING")
    early = _entry(902, opp="opp_e", event="ev_e", v1=1.0, v2=1.0, at=50.0)
    unresolved = _entry(903, opp="opp_u", event="ev_u", v1=1.0, v2=1.0,
                        at=500.0)
    outcomes[unresolved["intent_id"]] = {"state": "UNRESOLVED",
                                         "why": "POSITION_OPEN"}
    unscored = _entry(904, opp="opp_n", event="ev_n", v1=None, v2=None,
                      at=500.0)
    outcomes[unscored["intent_id"]] = {"state": "RESOLVED",
                                       "realized_net_usd": 3.0}
    got = OT.compute(entries + [re_, training, early, unresolved, unscored],
                     outcomes, since=100.0, cutover=60.0)
    c = got["counts"]
    assert got["entries"] == 8                  # TRAINING and pre-since out
    assert c["reevaluations"] == 1 and c["unresolved"] == 1
    assert c["unresolved_why"] == {"POSITION_OPEN": 1}
    assert c["resolved"] == 6
    rows, _ = OT.opportunities(entries + [re_], outcomes)
    first = next(r for r in rows if r["opportunity_id"] == "opp_0_0")
    assert first["intents"] == 2 and first["v2"] != 99.0
    assert first["realized_net_usd"] == pytest.approx(
        outcomes[entries[0]["intent_id"]]["realized_net_usd"] + 1.0)
    assert got["coverage"]["v1_unavailable_why"] == {"X": 1}


def test_an_entry_is_built_from_the_intent_and_never_scores_a_missing_value():
    it = _intent(intent_id="cdi_" + "1" * 24)
    e = OT.build_entry(intent=it, v1={"status": "UNAVAILABLE", "why": "NO"},
                       v2=_v2())
    assert e["entry_id"].startswith("ost_") and e["intent_sha"] == "a" * 64
    assert e["v1_status"] == "UNAVAILABLE" and e["v1_score"] is None
    assert e["v1_why"] == "NO"
    assert e["v2_status"] == "MEASURED" and e["v2_spec_sha"] == V2.SPEC_SHA
    assert e["authority"] == "SHADOW_NO_AUTHORITY"
    none = OT.build_entry(intent=it, v1=None, v2=None)
    assert none["v1_why"] == "V1_NOT_COMPUTED"
    assert none["v2_why"] == "V2_NOT_COMPUTED"
    assert OT.v1_predicted_net({"expected_net_executable_ev_usd": 10,
                                "fill_probability": 0.5,
                                "capacity_factor": 1.0}) == 5.0


# ── §3 the real writers ──────────────────────────────────────────────

@pg
async def test_the_outcome_join_reads_the_ledger_of_the_intents_own_order():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await F.prepare(conn)
        await F.record_cutover(conn)
        acct = await H.new_account(conn, "ost")
        T = time.time()
        tag = uuid.uuid4().hex[:8]
        a = await F.decision(conn, acct, T=T, slug="test-ost-a-%s" % tag,
                             event="ev-a-%s" % tag,
                             offers=[(0.50, 1500), (0.52, 1000)],
                             bids=[(0.48, 2000)])
        assert (await F.fill(conn, a, at=T + 4,
                             offers=[(0.50, 1500), (0.52, 1000)],
                             bids=[(0.48, 2000)]))["state"] == "FILLED"
        b = await F.decision(conn, acct, T=T + 5, slug="test-ost-b-%s" % tag,
                             event="ev-b-%s" % tag, offers=[(0.60, 3000)],
                             bids=[(0.58, 3000)])
        await F.fill(conn, b, at=T + 9, offers=[(0.60, 3000)],
                     bids=[(0.58, 3000)])
        c = await F.decision(conn, acct, T=T + 20, slug="test-ost-b-%s" % tag,
                             event="ev-b-%s" % tag, offers=[(0.50, 3000)],
                             bids=[(0.48, 3000)])
        assert (await F.fill(conn, c, at=T + 24, offers=[(0.50, 3000)],
                             bids=[(0.48, 3000)]))["state"] == "FILLED"
        assert all(d["tournament_recorded"] for d in (a, b, c))
        assert (await F.settle(conn, acct, a, outcome="WON",
                               at=T + 400))["ok"]
        assert (await F.settle(conn, acct, c, outcome="LOST",
                               at=T + 400))["ok"]
        mine = {d["intent"]["intent_id"] for d in (a, b, c)}
        rows = {r["intent_id"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM opportunity_score_tournament WHERE "
            " intent_id = ANY($1)", list(mine))}
        for d in (a, b, c):
            r = rows[d["intent"]["intent_id"]]
            assert r["intent_sha"] == d["intent"]["content_sha"]
            assert r["intent_version"] == d["intent"]["intent_version"]
            assert r["authority"] == "SHADOW_NO_AUTHORITY"
            assert r["v2_spec_sha"] == V2.SPEC_SHA
            assert r["v2_status"] == "MEASURED", r["v2_why"]
            assert r["v1_status"] == "MEASURED", r["v1_why"]
            assert r["sleeve"] == "INVESTMENT"
        assert rows[b["intent"]["intent_id"]]["opportunity_id"] == \
            rows[c["intent"]["intent_id"]]["opportunity_id"]
        entries, outcomes = await CT.gather(conn, since=T - 1)
        own = [e for e in entries if e["intent_id"] in mine]
        assert len(own) == 3
        from sportsassets import bettor_paper_ledger as L
        pos = {p["group_id"]: p for p in await L.positions(
            conn, acct["account_id"], include_closed=True)}
        oa = outcomes[a["intent"]["intent_id"]]
        assert oa["state"] == "RESOLVED"
        assert oa["realized_net_usd"] == pytest.approx(
            pos[a["group_id"]]["realized_pnl_usd"])
        # 2000 at a 0.505 VWAP + 0.005 fees, paid $1: net = 2000 - 1010 - 10
        assert oa["realized_net_usd"] == pytest.approx(980.0)
        ob = outcomes[b["intent"]["intent_id"]]
        assert ob == {"state": "RESOLVED", "why": None,
                      "realized_net_usd": 0.0,
                      "basis": "TERMINAL_ORDER_NEVER_FILLED_MEASURED_ZERO"}
        oc = outcomes[c["intent"]["intent_id"]]
        assert oc["realized_net_usd"] == pytest.approx(-1010.0)
        opp, counts = OT.opportunities(own, outcomes)
        assert counts["opportunities"] == 2 and counts["reevaluations"] == 1
        bc = next(r for r in opp if r["intents"] == 2)
        assert bc["realized_net_usd"] == pytest.approx(-1010.0)
        assert bc["v2"] == pytest.approx(
            rows[b["intent"]["intent_id"]]["v2_score"])
        cut = await CT.cutover_epoch(conn)
        got = OT.compute(own, outcomes, since=T - 1, cutover=cut)
        assert got["status"] == "OK" and got["counts"]["resolved"] == 2
        assert got["common"]["V2"]["status"] == "UNAVAILABLE"
        assert got["promotion_evidence"]["status"] == \
            "INSUFFICIENT_OUT_OF_SAMPLE_EVIDENCE"
        rep = await CT.report(conn, since=T - 1, now=T + 500)
        assert rep["status"] == "OK" and rep["sleeve"] == "INVESTMENT"
        assert rep["v2_authority"].startswith("NONE")
    finally:
        await tx.rollback()
        await conn.close()


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


@pg
async def test_the_database_refuses_backfill_authority_and_any_change():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await F.prepare(conn)
        acct = await H.new_account(conn, "ostdb")
        tag = uuid.uuid4().hex[:8]
        d = await F.decision(conn, acct, T=time.time(),
                             slug="test-ostdb-%s" % tag, event="ev-%s" % tag,
                             offers=[(0.50, 3000)], bids=[(0.48, 3000)])
        iid = d["intent"]["intent_id"]
        for sql in ("UPDATE opportunity_score_tournament SET v2_score = 1 "
                    " WHERE intent_id = $1",
                    "DELETE FROM opportunity_score_tournament "
                    " WHERE intent_id = $1"):
            await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                          sql, iid)
        await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                      "TRUNCATE opportunity_score_tournament")
        # idempotent on the intent: a second record is a no-op
        assert await OT.record_entry(conn, intent=d["intent"], v1=d["v1"],
                                     v2=d["v2"]) is False
        # a BACKFILL (scored long after its decision) is refused -- the
        # writer reports it and never raises into the decision
        old = dict(d["intent"], intent_id="cdi_" + uuid.uuid4().hex[:24],
                   created_at=time.time() - 3600)
        assert await OT.record_entry(conn, intent=old, v1=d["v1"],
                                     v2=d["v2"]) is False
        assert await conn.fetchval(
            "SELECT count(*) FROM opportunity_score_tournament WHERE "
            " intent_id = $1", old["intent_id"]) == 0
        base = ("INSERT INTO opportunity_score_tournament (entry_id, "
                " tournament_version, intent_id, intent_version, intent_sha,"
                " decision_id, opportunity_id, opportunity_key_version, "
                " opportunity_key, event_key, strategy, sleeve, "
                " us_market_slug, holding_side, decided_at, v1_version, "
                " v1_status, v1_score, v1_why, v1_detail, v2_version, "
                " v2_spec_sha, v2_status, v2_score, v2_why, v2_detail, "
                " authority) VALUES ('ost_' || repeat('c', 24), 'v', "
                " 'cdi_' || repeat('c', 24), 'v', repeat('a', 64), 'd', 'o',"
                " 'k', '{}', 'e', 's', 'INVESTMENT', 'm', 'LONG', now(), "
                " 'v1', %s, '{}', 'v2', repeat('a', 64), %s, '{}', %s)")
        ok_v1 = "'MEASURED', 0.1, NULL"
        ok_v2 = "'MEASURED', 0.2, NULL"
        await conn.execute(base % (ok_v1, ok_v2, "'SHADOW_NO_AUTHORITY'"))
        for v1, v2, auth in (
                ("'MEASURED', NULL, NULL", ok_v2, "'SHADOW_NO_AUTHORITY'"),
                (ok_v1, "'UNAVAILABLE', NULL, NULL",
                 "'SHADOW_NO_AUTHORITY'"),
                (ok_v1, "'UNAVAILABLE', 0.0, 'why'",
                 "'SHADOW_NO_AUTHORITY'"),
                (ok_v1, ok_v2, "'PROMOTED'")):
            await _expect(conn, (asyncpg.CheckViolationError,
                                 asyncpg.UniqueViolationError),
                          (base % (v1, v2, auth)).replace(
                              "repeat('c', 24)", "repeat('d', 24)"))
    finally:
        await tx.rollback()
        await conn.close()


from tests.test_live_parity_through_the_paper_pass import (  # noqa: E402,F401
    _pass, cg_on_with_parity)
from tests import paper_live_fixture as PL                     # noqa: E402


@pg
async def test_the_decision_hook_scores_v1_and_v2_beside_the_intent(
        cg_on_with_parity):                                   # noqa: F811
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        from sportsassets import live_parity as LP
        ctl = await LP.control(conn)
        if ctl.get("halted"):
            await LP.clear_halt(conn, actor="test harness (human operator)",
                                reason="isolate this test")
        if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
            await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
        await conn.execute(
            "INSERT INTO execmirror_snapshots (at, balances, positions, "
            " open_orders) VALUES (now(), $1::jsonb, '[]', 0)",
            json.dumps([{"currency": "USD", "buyingPower": 500}]))
        acct = await PL.new_account(conn, "ostpass", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        d = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"],
            v["valuation_id"], F.CG)
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        it = await conn.fetchrow(
            "SELECT * FROM canonical_decision_intents WHERE decision_id=$1",
            d["decision_id"])
        assert it is not None
        row = await conn.fetchrow(
            "SELECT * FROM opportunity_score_tournament WHERE intent_id=$1",
            it["intent_id"])
        assert row is not None, "the hook recorded no tournament entry"
        assert row["intent_sha"] == it["content_sha"]
        assert row["intent_version"] == it["intent_version"]
        assert row["decision_id"] == d["decision_id"]
        assert row["sleeve"] == "INVESTMENT"
        assert row["v2_spec_sha"] == V2.SPEC_SHA
        assert row["authority"] == "SHADOW_NO_AUTHORITY"
        for s in ("v1", "v2"):
            assert row[s + "_status"] in ("MEASURED", "UNAVAILABLE")
            if row[s + "_status"] == "UNAVAILABLE":
                assert row[s + "_why"] and row[s + "_score"] is None
        # scored at the decision instant, beside the intent -- never in it
        assert abs(row["decided_at"].timestamp()
                   - it["created_at"].timestamp()) < 1e-3
        assert "opportunity_score_v2" not in json.dumps(H.j(it["opportunity_score"]))
        opp = H.j(it["opportunity_score"])
        assert "V2" not in str(opp.get("version"))
        # the intent's execution estimates state what they were fitted on
        eddie = H.j(it["eddie"])
        if eddie.get("status") == "MEASURED":
            assert eddie["execution_evidence"]["fitted_on"] == \
                EE.PAPER_SIMULATION
            assert eddie["execution_evidence"]["actual"]["why"] == \
                EE.R_NO_ACTUAL
        allie = H.j(it["allie"])
        if "execution_evidence" in allie:
            assert allie["execution_evidence"]["actual"]["why"] == \
                EE.R_NO_ACTUAL
        assert client.mutation_attempts == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ── §4 no authority ──────────────────────────────────────────────────

WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE)\s+"
                   r"([a-z_]*)", re.I)


def _imports(path) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def _strings(path) -> list:
    return [n.value for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_nothing_on_the_decision_path_reads_the_tournament_or_v2():
    readers = []
    for p in PKG.rglob("*.py"):
        src = p.read_text()
        if "opportunity_score_tournament" in src:
            readers.append(str(p.relative_to(PKG)))
    assert sorted(readers) == ["api/command_opportunity_tournament.py",
                               "opportunity_tournament.py"], readers
    # V2's value reaches only the tournament writer: the hook passes it to
    # record_entry and nothing else reads `opportunity_score_v2`
    users = []
    for p in PKG.rglob("*.py"):
        if "opportunity_score_v2" in p.read_text():
            users.append(str(p.relative_to(PKG)))
    assert set(users) <= {"canonical_components.py", "live_parity.py",
                          "opportunity_tournament.py",
                          "execution_calibration.py",
                          "opportunity_score_v2.py"}, users
    lp = (PKG / "live_parity.py").read_text()
    assert lp.count('comps.get("opportunity_score_v2")') == 1
    assert "opportunity_score_v2" not in (
        PKG / "canonical_intent.py").read_text()
    for p in list((PKG / "agents").glob("paper_*.py")) + list(
            PKG.glob("bettor_paper_*.py")):
        assert "opportunity_score_v2" not in p.read_text(), p.name
        assert "opportunity_tournament" not in p.read_text(), p.name


def test_the_writers_write_only_the_tournament_and_v2_is_pure():
    for s in _strings(PKG / "opportunity_tournament.py"):
        for kw, table in WRITE.findall(s):
            assert kw.upper().startswith("INSERT"), s[:80]
            assert table == "opportunity_score_tournament", s[:80]
    for rel in ("api/command_opportunity_tournament.py",
                "opportunity_score_v2.py"):
        for s in _strings(PKG / rel):
            assert not WRITE.search(s), (rel, s[:80])
    assert _imports(PKG / "opportunity_score_v2.py") <= {
        "__future__", "annotations", "hashlib", "json", "math", "",
        "execution_evidence"}
    for rel in ("opportunity_tournament.py",
                "api/command_opportunity_tournament.py"):
        for imp in _imports(PKG / rel):
            leaf = imp.rsplit(".", 1)[-1]
            assert not any(f in leaf for f in (
                "execmirror", "kalshi", "pmus", "venue", "clob", "executor",
                "execution_intent", "execution_gate", "bettor_funded",
                "submit", "live_executor")), (rel, imp)


def test_the_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == CT.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(CT.PATH).status_code == 401
    assert client.get(CT.PATH + "?since=1").status_code == 401
    assert client.post(CT.PATH).status_code in (401, 405)
    src = (PKG / "api" / "command_opportunity_tournament.py").read_text()
    assert "readonly=not nested" in src and "statement_timeout" in src
