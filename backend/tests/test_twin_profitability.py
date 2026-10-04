"""PROFITABILITY EVIDENCE: TRANSFER RESEARCH, AGENT SCORECARDS, THE EVIDENCE
LADDER, ECONOMIC KILL SWITCHES AND THE EVALUATIONS -- ALL RESEARCH.

  §1 TRANSFER. A test is preregistered with source sport, target sport,
     hypothesis and frozen criteria; it is classified on FORWARD target-sport
     records only; below the predeclared forward sample it is UNKNOWN; the
     database refuses an in-sample window and a premature classification.
  §2 SCORECARDS. One per agent, economic contribution only; every metric
     carries numerator / denominator / sample / CI / status; unmeasured is
     null with a reason (the database refuses a 0 standing in for it, and a
     volume-of-analysis metric).
  §3 THE LADDER. Never skips a level (computed and CHECKed); confidence is
     UNPROVEN below level 4 (computed and CHECKed); the current level is
     computed from the records of the test database.
  §4 KILL SWITCHES. A triggered criterion writes ONLY a RECOMMEND_PAUSE
     record; no control table changes; the database refuses an applied,
     capital-stopping or other recommendation.
  §5 EVALS. Judged on persisted records: cited ids that do not resolve fail
     grounding; the macro trace names the stage a failure originated in.

ALL DATA IS SYNTHETIC.
"""
from __future__ import annotations

import time

import asyncpg
import pytest

from sportsassets.twin import common as C
from sportsassets.twin import evals as EV
from sportsassets.twin import killswitch as KS
from sportsassets.twin import ladder as LD
from sportsassets.twin import runner as RUN
from sportsassets.twin import scorecards as SCD
from sportsassets.twin import store as ST
from sportsassets.twin import transfer as TR

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
    from tests import twin_fixture as TF
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H
    import twin_fixture as TF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


# ── §1 transfer ──────────────────────────────────────────────────────

def _preds(sport, n, at0, p=0.6, wins=None, step=1.0):
    wins = int(round(n * p)) if wins is None else wins
    return [{"id": hash((sport, at0, i)) & 0xFFFFFFF, "sport_family": sport,
             "probability": p, "outcome": 1 if i < wins else 0,
             "us_market_slug": "%s-%d" % (sport, i), "at": at0 + i * step}
            for i in range(n)]


def _obs(preds):
    return TR.observations(preds=preds, books=[], actual_positions=[],
                           premap={}, markouts={})


def test_preregistration_names_source_target_hypothesis_and_criteria():
    now = TF.T0
    obs = _obs(_preds("baseball", 120, now - 10000)
               + _preds("basketball", 20, now - 5000))
    plans = TR.plan_registrations(obs, existing=set(), now=now,
                                  spec_id="TRANSFER_CRITERIA:v1:x")
    pb = [p for p in plans if p["dimension"] == "PROBABILITY_BANDS"]
    assert len(pb) == 1
    t = pb[0]
    assert (t["source_sport"], t["target_sport"]) == ("baseball",
                                                      "basketball")
    assert t["source_n"] == 120 and t["source_value"] is not None
    assert "baseball" in t["hypothesis"] and "basketball" in t["hypothesis"]
    assert t["criteria"]["min_forward_n"] == 100
    assert t["declared_at"] == now == t["source_window_end"]
    # already registered for that target -> not again
    assert not [p for p in TR.plan_registrations(
        obs, existing={("PROBABILITY_BANDS", "basketball")}, now=now,
        spec_id="s") if p["dimension"] == "PROBABILITY_BANDS"]


def _test(now, value, low, high, dim="PROBABILITY_BANDS"):
    return {"test_id": "twintx:t", "dimension": dim,
            "source_sport": "baseball", "target_sport": "basketball",
            "criteria": TR.criteria_of(dim), "source_value": value,
            "source_ci_low": low, "source_ci_high": high, "declared_at": now}


def test_classification_uses_forward_target_records_only():
    now = TF.T0
    src = TR.measure("PROBABILITY_BANDS", _obs(_preds(
        "baseball", 400, now - 99999))["PROBABILITY_BANDS"])
    t = _test(now, src["value"], src["low"], src["high"])
    # 500 in-sample basketball records BEFORE the declaration: ignored
    before = _obs(_preds("basketball", 500, now - 50000, p=0.9, wins=0))
    ev = TR.evaluate(t, before)
    assert ev["forward_n"] == 0 and ev["classification"] == "UNKNOWN"
    assert ev["reason"].startswith("FORWARD_SAMPLE_SHORT")
    # a short forward sample: UNKNOWN, never assumed
    ev = TR.evaluate(t, _obs(_preds("basketball", 40, now + 1)))
    assert ev["forward_n"] == 40 and ev["classification"] == "UNKNOWN"
    # the same calibration forward: TRANSFERABLE
    ev = TR.evaluate(t, _obs(_preds("basketball", 3000, now + 1)))
    assert ev["classification"] == "TRANSFERABLE", ev
    # badly calibrated forward: SPORT_SPECIFIC
    ev = TR.evaluate(t, _obs(_preds("basketball", 600, now + 1, p=0.9,
                                    wins=60)))
    assert ev["classification"] == "SPORT_SPECIFIC", ev
    # another sport's forward records never count for this target
    ev = TR.evaluate(t, _obs(_preds("hockey", 3000, now + 1)))
    assert ev["forward_n"] == 0


@pg
async def test_the_database_refuses_in_sample_or_premature_classification():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        spec = await ST.register_spec(conn, kind="TRANSFER_CRITERIA",
                                      version=1, spec=TR.CRITERIA_SPEC,
                                      now=TF.T0)
        cr = TR.criteria_of("LIQUIDITY")
        t = {"test_id": "twintx:dbtest", "dimension": "LIQUIDITY",
             "source_sport": "baseball", "target_sport": "soccer",
             "hypothesis": "h", "metric": cr["metric"], "criteria": cr,
             "criteria_sha256": C.sha(cr), "criteria_spec_id":
             spec["spec_id"], "declared_at": TF.T0,
             "source_window_end": TF.T0, "source_n": 60,
             "source_value": 3.0, "source_ci_low": 2.9,
             "source_ci_high": 3.1}
        assert await ST.register_transfer_test(conn, t)
        assert not await ST.register_transfer_test(conn, t)
        base = {"test_id": t["test_id"], "forward_start": TF.T0,
                "forward_n": 5, "target_value": 3.0, "target_ci_low": 2.9,
                "target_ci_high": 3.1, "diff": 0.0, "diff_ci_low": -0.1,
                "diff_ci_high": 0.1, "classification": "UNKNOWN",
                "reason": "r", "input_sha256": "a" * 64}
        assert await ST.save_transfer_evaluation(conn, run_id="r",
                                                 now=TF.T0 + 9, ev=base)
        for bad in (dict(base, forward_start=TF.T0 - 1,
                         input_sha256="b" * 64),
                    dict(base, classification="TRANSFERABLE",
                         input_sha256="c" * 64)):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.RaiseError):
                await ST.save_transfer_evaluation(conn, run_id="r",
                                                  now=TF.T0 + 9, ev=bad)
            await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute("UPDATE twin_transfer_tests SET "
                               " source_value = 9 WHERE test_id=$1",
                               t["test_id"])
        await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


# ── §2 scorecards ────────────────────────────────────────────────────

AGENTS = ("DEREK", "XAVIER", "EDDIE", "SCOUT", "KAREN", "ALLOCATOR",
          "AUDREY")


def _cards(st=None, **kw):
    from sportsassets.twin import engine as E
    from sportsassets.twin import scenarios as SC
    st = st or TF.stream()
    results, traces = {}, {}
    for sc in SC.catalog():
        r = E.run_scenario(st, sc)
        results.setdefault(sc["scenario_key"], {})["PAPER"] = r["body"]
        traces[(sc["scenario_key"], "PAPER")] = r["rows"]
    db = kw.get("db") or {"value_add": [], "karen": None, "audrey": {}}
    return SCD.compute(streams={"PAPER": st}, results=results,
                       traces=traces, db=db, eddie_rows=None,
                       eddie_why="INTERFACE_ABSENT:pos_iface_eddie_execution",
                       scout_rows=None,
                       scout_why="INTERFACE_ABSENT:pos_iface_scout")


def test_every_agent_has_a_scorecard_and_every_metric_its_shape():
    rows = _cards()
    assert {r["agent"] for r in rows} == set(AGENTS)
    for r in rows:
        for k in ("value", "numerator", "denominator", "sample_n", "ci_low",
                  "ci_high", "status", "reason", "basis", "book"):
            assert k in r, (r["agent"], r["metric"], k)
        assert r["status"] in C.STATUSES
        if r["value"] is None:
            assert r["status"] in ("UNAVAILABLE", "UNPROVEN") and r["reason"]
        assert r["book"] in ("PAPER", "ACTUAL", "COUNTERFACTUAL")
        for word in ("volume", "messages", "reports_written"):
            assert word not in r["metric"]


def test_derek_and_xavier_economics_on_the_synthetic_stream():
    rows = {(r["agent"], r["metric"]): r for r in _cards()}
    pred = rows[("DEREK", "predicted_edge_per_contract")]
    # settled: A (.62 - .52 = .10) and C (.60 - .50 = .10)
    assert pred["value"] == pytest.approx(0.10)
    assert pred["sample_n"] == 2 and pred["status"] == "INSUFFICIENT_SAMPLE"
    real = rows[("DEREK", "realized_edge_per_contract")]
    assert real["value"] == pytest.approx((0.46 + 0.08) / 2)
    acc = rows[("DEREK", "accepted_opportunity_pnl")]
    assert acc["numerator"] == pytest.approx(54.0)
    # B was refused and its contract paid: a false negative
    fn = rows[("DEREK", "false_negative_rate")]
    assert (fn["numerator"], fn["denominator"]) == (1, 1)
    inc = rows[("XAVIER", "incremental_pnl_vs_hold")]
    # A and the open D held without action (measured 0, as the attribution
    # identity says), C managed (8 - (-51) = 59)
    assert inc["numerator"] == pytest.approx(59.0) and inc["sample_n"] == 3
    good = rows[("XAVIER", "good_exit_rate")]
    assert (good["numerator"], good["denominator"]) == (1, 1)
    assert rows[("XAVIER", "reallocate_value")]["status"] == "UNAVAILABLE"
    for m in SCD.EDDIE_METRICS:
        e = rows[("EDDIE", m)]
        assert e["value"] is None and e["reason"].startswith(
            "INTERFACE_ABSENT")


@pg
async def test_the_database_refuses_a_zero_for_unmeasured_and_volume():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        ok = C.metric("DEREK", "calibration_brier", book="PAPER", basis="b",
                      reason="NO_ORDINARY_SETTLEMENT")
        await ST.save_scorecards(conn, run_id="r1", now=TF.T0, rows=[ok])
        for bad in (dict(ok, run_id="r2", value=0.0),
                    dict(ok, status="MEASURED", value=None),
                    dict(ok, metric="analyses_volume", value=3.0,
                         status="MEASURED")):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await ST.save_scorecards(conn, run_id="rbad", now=TF.T0,
                                         rows=[bad])
            await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


# ── §3 the ladder ────────────────────────────────────────────────────

def _closed(n, *, start, pnl=1.0, sport="baseball"):
    return [{"key": "k%d" % i, "opened_at": start + 10 + i,
             "closed_at": start + 20 + i, "fixture": "fx-%s-%d" % (sport, i),
             "realized_pnl_usd": pnl, "cost_usd": 50.0, "sport": sport,
             "decided_at": start + 5 + i, "p": 0.6, "payoff": 1.0,
             "slippage_pc": 0.01} for i in range(n)]


FWD = C.epoch(LD._iso(C.FORWARD_SAMPLE_RULE["forward_start"]))


def test_the_ladder_never_skips_a_level():
    now = FWD + 40 * 86400
    # plenty of forward paper and actual evidence, but no history (level 1)
    out = LD.compute(history={"scored": 5, "brier_n": 5},
                     paper_closed=_closed(50, start=FWD),
                     actual_closed=_closed(20, start=FWD),
                     actual_positions=[{"slippage_pc": 0.01}] * 20,
                     regimes=[], now=now)
    assert out["passes"][:4] == [True, False, True, True]
    assert out["level"] == 0 and out["skipped_levels"] is False
    out = LD.compute(history={"scored": 150, "brier_n": 100},
                     paper_closed=_closed(50, start=FWD),
                     actual_closed=_closed(20, start=FWD),
                     actual_positions=[{"slippage_pc": 0.01}] * 20,
                     regimes=[], now=now)
    assert out["level"] == 3
    assert out["confidence"]["status"] == "UNPROVEN"
    st = out["confidence"]["statement"]
    assert "%" not in st and "confident" not in st.lower()
    assert out["confidence"]["never_a_percentage"] is True


def test_confidence_is_measured_and_needs_level_four():
    now = FWD + 40 * 86400
    win = _closed(320, start=FWD, pnl=2.0)
    for i, r in enumerate(win):
        r["realized_pnl_usd"] = 2.0 + (i % 5) - 2.0     # mean 2, varies
    out = LD.compute(history={"scored": 150, "brier_n": 100},
                     paper_closed=_closed(50, start=FWD),
                     actual_closed=win,
                     actual_positions=[{"slippage_pc": 0.01}] * 20,
                     regimes=[], now=now)
    assert out["level"] == 4 and out["confidence"]["status"] == "LOW"
    c = out["confidence"]
    assert c["sample_size"] == 320
    assert c["uncertainty"]["one_sided_lower_95"] > 0
    assert c["uncertainty"]["p_value_one_sided_mean_le_0"] < 0.05
    assert c["calibration_brier"] == pytest.approx(0.16)


@pg
async def test_the_database_enforces_the_ladder():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        cs = await ST.register_spec(conn, kind="LADDER_CRITERIA", version=1,
                                    spec=LD.LADDER_SPEC, now=TF.T0)
        cf = await ST.register_spec(conn, kind="CONFIDENCE_RULES", version=1,
                                    spec=LD.CONFIDENCE_SPEC, now=TF.T0)
        good = {"level": 1, "passes": [True, True, False, True, False,
                                       False, False], "levels": [],
                "criteria_spec_id": cs["spec_id"],
                "criteria_sha256": cs["spec_sha256"],
                "confidence": {"status": "UNPROVEN"},
                "confidence_spec_id": cf["spec_id"]}
        await ST.save_ladder(conn, run_id="l1", now=TF.T0, ladder=good)
        for rid, bad in (("l2", dict(good, level=3)),
                         ("l3", dict(good, confidence={"status": "LOW"}))):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await ST.save_ladder(conn, run_id=rid, now=TF.T0, ladder=bad)
            await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(ST.FrozenSpecChanged):
            await ST.register_spec(conn, kind="LADDER_CRITERIA", version=1,
                                   spec=dict(LD.LADDER_SPEC, rule="x"),
                                   now=TF.T0)
        await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


async def _seed_world(conn, now):
    acct = await H.new_account(conn, "twinprof", now=now - 86400)
    exp = F.uid("TWIN_PROF_")
    vid = await F.valuation(conn, experiment_id=exp, at=now - 4000, p=0.62,
                            outcome=1)
    d = await F.decision(conn, acct, at=now - 600, p=0.62, vwap=0.52,
                         fees_usd=1.0, qty=100, depth=500.0, valuation_id=vid)
    g = await F.position(conn, acct, slug=d["slug"], qty=100, price=0.53,
                         fee=1.0, at=now - 590, decision_id=d["decision_id"],
                         event_key=d["event_key"])
    await F.settle(conn, acct, group_id=g, slug=d["slug"], qty=100,
                   outcome="WON", payout_per_contract=1.0, at=now - 60)
    # a refused decision citing a valuation id that does not exist
    bad = await F.decision(conn, acct, at=now - 500, p=0.5, verdict="REFUSE",
                           valuation_id=987654321)
    return acct, d, g, bad


@pg
async def test_the_current_evidence_level_on_the_test_database():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, d, g, bad = await _seed_world(conn, now)
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  include_actual=False)
        assert set(got["components"].values()) == {"OK"}, got
        lad = await conn.fetchrow(
            "SELECT * FROM twin_evidence_ladder WHERE run_id=$1",
            got["run_id"])
        # one settled paper decision: not even a backtest sample -> level 0
        assert lad["level"] == 0
        assert list(lad["passes"])[0] is True
        assert list(lad["passes"])[1] is False
        assert lad["confidence_status"] == "UNPROVEN"
        cards = await conn.fetch(
            "SELECT agent, status, value, reason FROM twin_agent_scorecards"
            " WHERE run_id=$1", got["run_id"])
        assert {c["agent"] for c in cards} == set(AGENTS)
        assert all(c["reason"] for c in cards if c["value"] is None)
    finally:
        await tr.rollback()
        await conn.close()


# ── §4 kill switches ─────────────────────────────────────────────────

def test_a_triggered_criterion_is_only_a_recommend_pause_record():
    raw = TF.scenario_raw()
    t = TF.T0
    raw = {"decisions": [], "groups": {}, "fills": [], "settlements": [],
           "reviews": []}
    for i in range(60):                       # predicted .10, all lose
        did, g, s = "K%d" % i, "gK%d" % i, "syn-k%d" % i
        raw["decisions"].append(TF.decision(did, t + i, p=0.62, vwap=0.52,
                                            slug=s))
        raw["groups"][did] = g
        raw["fills"].append(TF.fill(g, t + i + 1, qty=100, price=0.52,
                                    fee=1.0, slug=s))
        raw["settlements"].append(TF.settlement(g, slug=s, qty=100,
                                                payout=0.0, at=t + 5000 + i))
    got = KS.evaluate(streams={"PAPER": TF.stream(raw=raw, books=[])},
                      regimes=[], tournament_rows=None,
                      tournament_why="INTERFACE_ABSENT:x",
                      spec_id="KILL_SWITCH_CRITERIA:v1:x", spec_sha="0" * 64)
    trig = {e["criterion"] for e in got["evaluations"]
            if e["status"] == "TRIGGERED"}
    assert "REALIZED_EDGE_BELOW_PREDICTED" in trig
    assert "CALIBRATION_FAILURE" in trig
    assert all(r["recommendation"] == "RECOMMEND_PAUSE"
               for r in got["recommendations"])
    assert {e["status"] for e in got["evaluations"]
            if e["criterion"] == "CHALLENGER_BEATS_CHAMPION"} == {
        "UNAVAILABLE"}
    assert {e["status"] for e in got["evaluations"]
            if e["criterion"] == "REGIME_SHIFT"} == {"UNAVAILABLE"}


def test_regime_and_tournament_criteria():
    r = KS.regime_criteria([{"run_id": "a", "recommendation": "REDUCE",
                             "at": 1}, {"run_id": "b",
                                        "recommendation": "REDUCE", "at": 2},
                            {"run_id": "c", "recommendation": "REDUCE",
                             "at": 3}])
    assert r[0]["status"] == "TRIGGERED"
    r = KS.regime_criteria([{"run_id": "a", "recommendation": "NORMAL",
                             "at": 1}])
    assert r[0]["status"] == "NOT_TRIGGERED"
    t = KS.tournament_criteria([{"tournament_id": "t", "kind": "MODEL",
                                 "champion": "S1", "challenger": "S2",
                                 "verdict": "CHALLENGER_BEATS_CHAMPION",
                                 "evidence_n": 150, "p_value": 0.01,
                                 "at": 1}], None)
    assert t[0]["status"] == "TRIGGERED" and t[0]["strategy"] == "S1"
    t = KS.tournament_criteria([{"verdict": "CHALLENGER_BEATS_CHAMPION",
                                 "evidence_n": 20, "p_value": 0.01}], None)
    assert t[0]["status"] == "NOT_TRIGGERED"


@pg
async def test_a_cycle_recommends_a_pause_and_changes_no_control_table():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, d, g, bad = await _seed_world(conn, now)
        for i in range(3):
            await conn.execute(
                "INSERT INTO intel_regime_states (run_id, computed_at, "
                " recommendation, reasons, signals) VALUES ($1, "
                " to_timestamp($2), 'REDUCE', '[]', '{}')",
                F.uid("twin-regime-"), now - 300 + i)
        before = await TF.control_snapshot(conn)
        prot = await F.protected_counts(conn)
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  include_actual=False)
        assert got["components"]["KILL_SWITCHES"] == "OK", got
        assert await TF.control_snapshot(conn) == before
        assert await F.protected_counts(conn) == prot
        rec = await conn.fetchrow(
            "SELECT * FROM twin_kill_switch_recommendations "
            " WHERE run_id=$1 AND criterion='REGIME_SHIFT'", got["run_id"])
        assert rec["recommendation"] == "RECOMMEND_PAUSE"
        assert rec["applied"] is False and rec["stops_capital"] is False
        assert rec["activates_capital"] is False
        assert rec["authority"] == "RESEARCH_NO_AUTHORITY"
        # the same evidence is not recommended twice
        again = await RUN.run_cycle(conn, now=now,
                                    account_id=acct["account_id"],
                                    include_actual=False)
        assert await conn.fetchval(
            "SELECT count(*) FROM twin_kill_switch_recommendations "
            " WHERE run_id=$1", again["run_id"]) == 0
        for col, val in (("recommendation", "'PAUSE_NOW'"),
                         ("applied", "true"), ("stops_capital", "true"),
                         ("activates_capital", "true")):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO twin_kill_switch_recommendations ("
                    " recommendation_id, run_id, criterion, book, strategy,"
                    " evidence, evidence_sha256, criteria_spec_id, "
                    " criteria_sha256, created_at, %s) SELECT 'x', 'r', "
                    " 'REGIME_SHIFT', 'NONE', '*', '{}', repeat('b', 64), "
                    " criteria_spec_id, criteria_sha256, now(), %s FROM "
                    " twin_kill_switch_recommendations LIMIT 1" % (col, val))
            await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


# ── §5 evals ─────────────────────────────────────────────────────────

def test_karen_grounding_resolves_cited_ids():
    db = {"karen": [
        {"challenge_id": "c1", "evidence_refs": [
            {"kind": "paper_decisions", "id": "A"}], "claim": "x",
         "severity": "HIGH", "state": "OPEN", "outcome": None,
         "resolved_by": None, "production_effect": "NONE", "blocked": False,
         "false_block": None},
        {"challenge_id": "c2", "evidence_refs": [
            {"kind": "paper_decisions", "id": "ghost"},
            {"kind": "some_other_kind", "id": "z"}], "claim": "y",
         "severity": "LOW", "state": "OPEN", "outcome": None,
         "resolved_by": None, "production_effect": "NONE", "blocked": False,
         "false_block": None}],
        "resolved_refs": {"paper_decisions": {"A"}}}
    rows = {r["dimension"]: r for r in EV.karen(db)}
    g = rows["EVIDENCE_GROUNDING"]
    assert (g["n_evaluated"], g["n_passed"]) == (2, 1)
    assert g["failures"] == ["c2"]
    assert g["detail"]["refs_of_unresolvable_kind"] == 1
    assert rows["AUTHORITY_COMPLIANCE"]["n_passed"] == 2


def test_the_macro_trace_locates_where_a_failure_originates():
    st = TF.stream()
    pos = st.positions
    decs = [{"decision_id": "A", "verdict": "ENTER", "has_p": True,
             "vwap": 0.52, "at": TF.T0},
            {"decision_id": "C", "verdict": "ENTER", "has_p": True,
             "vwap": 0.50, "at": TF.T0 + 20},
            {"decision_id": "X", "verdict": "ENTER", "has_p": False,
             "vwap": None, "at": TF.T0 + 25},
            {"decision_id": "Y", "verdict": "ENTER", "has_p": True,
             "vwap": 0.5, "at": TF.T0 + 26}]
    db = {"decisions": decs, "allocation_runs": [TF.T0 + 1000],
          "allocated_decisions": {"A", "C", "Y"},
          "reviewed_groups": {"gA"}, "postmortem_groups": {"gA"}}
    m = EV.macro(db, pos, None)
    by = {t["decision_id"]: t["origin"] for t in m["traces"]}
    assert by == {"A": None, "C": "XAVIER", "X": "DEREK",
                  "Y": "EXECUTION"}
    assert m["origins"] == {"DEREK": 1, "EXECUTION": 1, "NONE": 1,
                            "XAVIER": 1}


@pg
async def test_evals_judge_persisted_records_through_the_cycle():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, d, g, bad = await _seed_world(conn, now)
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  include_actual=False)
        rows = {(r["scope"], r["subject"], r["dimension"]): dict(r)
                for r in await conn.fetch(
                    "SELECT * FROM twin_evals WHERE run_id=$1",
                    got["run_id"])}
        for a in AGENTS:
            for dim in EV.AGENT_DIMENSIONS:
                assert ("AGENT", a, dim) in rows, (a, dim)
        gr = rows[("AGENT", "DEREK", "EVIDENCE_GROUNDING")]
        assert bad["decision_id"] in gr["failures"]
        assert gr["n_passed"] < gr["n_evaluated"]
        ed = rows[("AGENT", "EDDIE", "EVIDENCE_GROUNDING")]
        assert ed["status"] == "UNAVAILABLE"
        assert ed["reason"].startswith("INTERFACE_ABSENT")
        assert ("MACRO", "DEREK", "FAILURE_ORIGIN") in rows
        assert ("HANDOFF", "DEREK->XAVIER", "HANDOFF_QUALITY") in rows
    finally:
        await tr.rollback()
        await conn.close()
