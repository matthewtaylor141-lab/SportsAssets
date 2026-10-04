"""CAPITAL-CRITICAL: THE ANTI-OVERFITTING DEFENCES OF THE LEARNING LAYER,
ONE TEST (OR MORE) PER DEFENCE. research/pos_learn_audit.md maps each
defence to its mechanism and to these tests.

  D1  look-ahead                 D7  selection bias
  D2  train/test leakage         D8  regime overfitting
  D3  post-outcome features      D9  sport leakage
  D4  survivorship               D10 counterfactual contamination
  D5  multiple-hypothesis tests  D11 agent confirmation loops
  D6  p-hacking

ALL DATA IS SYNTHETIC.
"""
from __future__ import annotations

import inspect
import random
import time

import asyncpg
import pytest

from sportsassets.poslearn import agents as AG
from sportsassets.poslearn import avoidance as AV
from sportsassets.poslearn import common as C
from sportsassets.poslearn import edge_confidence as EC
from sportsassets.poslearn import experiments as EX
from sportsassets.poslearn import features as FT
from sportsassets.poslearn import karen_review as KAR
from sportsassets.poslearn import models as M
from sportsassets.poslearn import reads as R
from sportsassets.poslearn import runner as RUN
from sportsassets.poslearn import scoring as S

try:
    from tests import poslearn_fixture as P
except ImportError:                                             # pragma: no cover
    import poslearn_fixture as P

pg = pytest.mark.skipif(not P.DSN, reason="needs RN1X_TEST_DSN")
T0 = 1_790_000_000.0


# ── D1 LOOK-AHEAD ───────────────────────────────────────────────────

def test_d1_quotes_at_or_after_t_never_enter_a_feature():
    row = {"probability": 0.6, "executable_price": 0.5,
           "cost_per_contract": 0.01, "buy_intent": "ORDER_INTENT_BUY_LONG"}
    early = FT.build(row, t=T0, quotes=[(T0 - 600, 0.55)])[0]
    late = FT.build(row, t=T0, quotes=[(T0 - 600, 0.55), (T0, 0.9),
                                       (T0 + 5, 0.1)])[0]
    for k in ("pinnapi_vol_1h", "change_freq_1h", "price_move_1h"):
        assert early[k] == late[k], k


def test_d1_the_history_index_cannot_see_the_future_or_go_back():
    h = FT.HistoryIndex([{"outcome_at": T0 + i, "p": 0.6, "o": 1,
                          "sport": "s"} for i in range(50)])
    h.advance(T0 + 24.5)
    assert h.known() == 25
    rel, n = h.reliability("s")
    assert n == 25 and rel == pytest.approx(0.4)
    with pytest.raises(ValueError):
        h.advance(T0)


@pg
async def test_d1_the_book_feature_is_as_of_t_only():
    conn, tr = await P.open_tx()
    try:
        from tests import paper_harness as H
        t = time.time()
        slug = P.uid("pl-book-")
        await H.observe(conn, slug, t - 30, bids=((0.40, 10),),
                        offers=((0.42, 10),))
        await H.observe(conn, slug, t + 30, bids=((0.10, 10),),
                        offers=((0.90, 10),))
        got = await R.books_asof(conn, [(slug, t)], max_age_s=300)
        view = C.IC.book_view(got[(slug, t)]["bids"],
                              got[(slug, t)]["offers"])
        assert view["spread"] == pytest.approx(0.02)
        stale = await R.books_asof(conn, [(slug, t - 400)], max_age_s=300)
        assert stale == {}
        # and the database refuses features stamped after the opportunity
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO poslearn_opportunities (opportunity_id, "
                " source_kind, source_id, unit, opportunity_at, "
                " features_as_of, captured_at, sport, league, market, "
                " live_state, features) VALUES ('d1','EXTERNAL_VALUATION',-1,"
                " 'd1',now(),now() + interval '1 s',now() + interval '2 s',"
                " 's','l','m','PREGAME','{}')")
        await sp.rollback()
    finally:
        await P.close_tx(conn, tr)


# ── D2 TRAIN / TEST LEAKAGE ─────────────────────────────────────────

def _rows(n, *, sport="baseball", seed=3):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        p = (0.15, 0.85)[i % 2]
        out.append({"p": p, "p_reference": p, "o": int(rng.random() < 0.5 +
                                                       0.4 * (p - 0.5)),
                    "features": {"logit_p": C.logit(p)}, "sport": sport,
                    "league": "UNKNOWN", "sport_key": M.sport_key(sport,
                                                                  "UNKNOWN"),
                    "decided_at": T0 + i, "outcome_at": T0 + 1000 + i,
                    "price": 0.5, "fee": 0.01, "market": "h2h",
                    "live_state": "PREGAME"})
    return out


def test_d2_training_uses_only_outcomes_known_at_registration():
    rows = _rows(300)
    at = T0 + 1000 + 149.5                      # 150 outcomes known
    got = M.train("M1_PINNAPI_CALIBRATED", "CALIBRATED", rows, now=at)
    assert got["n"] == 150 and got["window"]["end"] <= at
    ec = EC.train(rows, now=at)
    assert ec["trainable"] is False             # 150 < 200: not trained
    # the scorer ignores a forecast row on an opportunity before registration
    opps = {"a": {"opportunity_at": T0 - 5, "price": 0.5, "fee": 0.0},
            "b": {"opportunity_at": T0 + 5, "price": 0.5, "fee": 0.0}}
    outs = {k: {"outcome_class": "RESOLVED", "o": 1} for k in opps}
    fcs = [{"opportunity_id": k, "probability": 0.7, "action": "SCORE"}
           for k in opps]
    rep = S.model_report({"registration_id": "X@1", "subject_id": "X",
                          "registered_at": T0, "status": "ACTIVE_FORWARD",
                          "document": {"role": "CHALLENGER"}}, fcs, opps,
                         outs)
    assert rep["offered"] == 1 and rep["resolved"] == 1


# ── D3 POST-OUTCOME FEATURES ────────────────────────────────────────

def test_d3_no_model_consumes_an_outcome_bearing_feature():
    bad = set(FT.FORBIDDEN_KEYS)
    for names in (FT.EC_FEATURES, M.M2_FEATURES, AV.DIMENSIONS):
        assert not bad & set(names), names
        assert not any("outcome" in n or "settle" in n and "family" not in n
                       for n in names), names
    src = inspect.getsource(FT.build)
    assert "outcome" not in src.split('"""', 2)[-1].replace(
        "FORBIDDEN_KEYS", "")


# ── D4 SURVIVORSHIP ─────────────────────────────────────────────────

@pg
async def test_d4_voided_and_unverified_outcomes_are_recorded_not_dropped():
    conn, tr = await P.open_tx()
    try:
        T = time.time()
        await RUN.run_cycle(conn, now=T)
        a = await P.valuation(conn, at=T + 5, p=0.8, price=0.6)
        b = await P.valuation(conn, at=T + 6, p=0.8, price=0.6)
        c = await P.valuation(conn, at=T + 7, p=0.8, price=0.6)
        # a fixture that resolves before any cycle sees it is COUNTED missed
        d = await P.valuation(conn, at=T + 8, p=0.8, price=0.6)
        await P.resolve(conn, d["id"], 1, at=T + 15)
        c2 = await RUN.run_cycle(conn, now=T + 20)
        summ = C.jload(await conn.fetchval(
            "SELECT summary FROM poslearn_runs WHERE run_id=$1 AND "
            " component='CAPTURE'", c2["run_id"]))
        assert summ["captured"] == 3
        assert summ["missed_outcome_known_before_capture"] == 1
        await conn.execute(
            "UPDATE external_valuations SET outcome_basis = 'CONFIRMED_VOID'"
            " WHERE id = $1", a["id"])
        await P.resolve(conn, b["id"], 1, at=T + 30, basis="UNVERIFIED_X")
        await P.resolve(conn, c["id"], 0, at=T + 30)
        await RUN.run_cycle(conn, now=T + 40)
        cls = dict(await conn.fetch(
            "SELECT o.source_id, x.outcome_class FROM poslearn_outcomes x "
            "  JOIN poslearn_opportunities o USING (opportunity_id)"))
        assert cls == {a["id"]: "VOID", b["id"]: "UNVERIFIED",
                       c["id"]: "RESOLVED"}
        snap = C.jload(await conn.fetchval(
            "SELECT payload FROM poslearn_snapshots WHERE component = "
            " 'AGENT_TOURNAMENT' ORDER BY computed_at DESC LIMIT 1"))
        derek = [v for v in snap["variants"]
                 if v["subject_id"] == "DEREK_V1"][0]
        assert derek["metrics"]["n"] == 2        # the VOID counts, as 0
    finally:
        await P.close_tx(conn, tr)


def test_d4_abstentions_count_against_coverage():
    opps = {"o%d" % i: {"opportunity_at": T0 + i, "price": 0.5,
                        "fee": 0.0} for i in range(4)}
    outs = {k: {"outcome_class": "RESOLVED", "o": 1} for k in opps}
    fcs = [{"opportunity_id": "o0", "probability": 0.6, "action": "SCORE"}] \
        + [{"opportunity_id": k, "probability": None, "action": "ABSTAIN"}
           for k in ("o1", "o2", "o3")]
    rep = S.model_report({"registration_id": "X@1", "subject_id": "X",
                          "registered_at": T0 - 1, "status": "ACTIVE_FORWARD",
                          "document": {"role": "CHALLENGER"}}, fcs, opps,
                         outs)
    assert rep["coverage"] == 0.25


# ── D5 MULTIPLE HYPOTHESES ──────────────────────────────────────────

def test_d5_family_sizes_are_the_declared_plans_and_thresholds_adjust():
    assert M.FAMILY_SIZE == len(M.SPECS) - 1         # trainable or not
    assert AG.FAMILY_SIZE == 8 and EX.FAMILY_SIZE == len(EX.PLAN)
    doc = M.document("M1_PINNAPI_CALIBRATED", "CALIBRATED",
                     {"window": None, "params": {}, "features": []},
                     version=1, threshold_pp=0.5, threshold_basis="T")
    assert doc["family_size"] == 11
    assert doc["promotion_threshold"]["z_adjusted"] > 2.8
    assert EX.power_n_per_arm(family_size=2) > EX.power_n_per_arm(
        family_size=1)
    got = AV.train([{"p_reference": 0.8, "price": 0.6, "fee": 0.01,
                     "o": i % 2, "sport": "s%d" % (i % 4), "league": "l",
                     "market": "m", "live_state": "PREGAME",
                     "outcome_at": T0, "features": {}} for i in range(400)],
                   now=T0 + 1, threshold_pp=0.5)
    assert got["params"]["z_adjusted_one_sided"] == pytest.approx(
        C.adjusted_z(0.05, got["params"]["segments_tested"],
                     two_sided=False), abs=1e-6)


# ── D6 P-HACKING ────────────────────────────────────────────────────

def test_d6_no_retry_until_it_passes():
    assert RUN._may_register(None) is True
    assert RUN._may_register({"status": "FAILED_FORWARD",
                              "status_reason": "PREDECLARED"}) is False
    assert RUN._may_register({"status": "RETIRED",
                              "status_reason": "manual"}) is False
    assert RUN._may_register({"status": "RETIRED", "status_reason":
                              RUN.RETIRE_POLICY + "_TO_1.00PP"}) is True
    assert RUN._may_register({"status": "ACTIVE_FORWARD"}) is False


def test_d6_the_verdict_uses_a_fixed_first_n_sample():
    src = inspect.getsource(S.paired)
    assert "rows[:n_min]" in src and "rows.sort()" in src
    assert "rows[n_min - 1][0]" in src          # coverage frozen at the cut


def test_d6_experiments_have_one_analysis_and_no_interim_estimate():
    d = EX.design(EX.PLAN[0], policy_versions={}, now=T0)
    assert d["stopping_rule"]["analyses"] == 1
    assert d["stopping_rule"]["early_stop_for_efficacy"] is False
    src = inspect.getsource(RUN.experiments)
    assert "NOT_SHOWN_BEFORE_THE_SINGLE_PREDECLARED_" in src


# ── D7 SELECTION BIAS ───────────────────────────────────────────────

def test_d7_management_and_sizing_variants_share_one_entry_set():
    opps = [{"opportunity_id": "o%d" % i, "p_reference": p, "price": 0.6,
             "fee": 0.01, "features": {}} for i, p in
            enumerate((0.9, 0.5, 0.7, 0.62))]
    sets = []
    for s in ("XAVIER_V1", "XAVIER_CHALLENGER_A", "XAVIER_CHALLENGER_B"):
        doc = AG.document(s, version=1, threshold_pp=0.5,
                          threshold_basis="T")
        sets.append({o["opportunity_id"] for o in opps
                     if AG.xavier(doc, o, threshold_pp=0.5)["action"]
                     != "ABSTAIN"})
    for s in ("ALLOCATOR_V1", "ALLOCATOR_CHALLENGER_A",
              "ALLOCATOR_CHALLENGER_B"):
        doc = AG.document(s, version=1, threshold_pp=0.5,
                          threshold_basis="T")
        got = AG.allocate(doc, opps, ec_by_opp={}, threshold_pp=0.5)
        sets.append({k for k, v in got.items() if v["action"] != "ABSTAIN"})
    assert all(x == sets[0] for x in sets) and sets[0] == {"o0", "o2", "o3"}


# ── D8 REGIME OVERFITTING ───────────────────────────────────────────

def test_d8_an_improvement_from_one_regime_or_one_half_is_blocked():
    doc = {"training_window": {"end": T0 - 10}}
    pairs = [{"at": T0 + i, "diff": (0.3 if i % 2 else -0.05),
              "sport": "s", "regime": ("REDUCE" if i % 2 else "NORMAL")}
             for i in range(80)]
    got = KAR.promotion_challenge(pairs, document=doc, registered_at=T0)
    assert "REGIME" in got["blocked_by"]
    halves = [dict(p, diff=(0.3 if i < 40 else -0.1), regime="NORMAL")
              for i, p in enumerate(pairs)]
    got = KAR.promotion_challenge(halves, document=doc, registered_at=T0)
    assert "TIME_STABILITY" in got["blocked_by"]


# ── D9 SPORT LEAKAGE ────────────────────────────────────────────────

def test_d9_a_sport_model_trains_on_its_own_sport_only():
    mlb = _rows(300, sport="baseball")
    nfl = M.train("M5_NFL_SPECIFIC", "SPORT:NFL", mlb, now=T0 + 10 ** 6)
    assert nfl["trainable"] is False
    assert nfl["why"] == "INSUFFICIENT_TRAINING_DATA_N_0_NEEDS_100"
    mixed = mlb + _rows(300, sport="soccer", seed=9)
    got = M.train("M5_MLB_SPECIFIC", "SPORT:MLB", mixed, now=T0 + 10 ** 6)
    assert got["n"] == 300
    pairs = [{"at": T0 + i, "diff": (0.4 if i % 3 == 0 else -0.05),
              "sport": ("A" if i % 3 == 0 else "B"), "regime": "N"}
             for i in range(90)]
    got = KAR.promotion_challenge(
        pairs, document={"training_window": "NOT_TRAINED"},
        registered_at=T0)
    assert "SPORT_LEAKAGE" in got["blocked_by"]


# ── D10 COUNTERFACTUAL CONTAMINATION ────────────────────────────────

def test_d10_assignment_depends_on_nothing_but_seed_and_unit():
    params = list(inspect.signature(EX.draw).parameters)
    assert params == ["seed", "experiment_id", "unit_id"]
    a = EX.draw("s" * 10, "E", "unit-1")
    assert a == EX.draw("s" * 10, "E", "unit-1")
    assert a != EX.draw("s" * 10, "E2", "unit-1")


def test_d10_meta_models_are_measured_on_the_unfiltered_champion_set():
    rows = [{"avoidance_level": lv, "p_reference": 0.8, "price": 0.6,
             "fee": 0.01, "o": 0} for lv in ("AVOID", "NORMAL", "CAUTION",
                                             "UNMEASURED")]
    got = AV.evaluate(rows, threshold_pp=0.5)
    assert got["champion_entries"] == 4         # not just the unblocked
    assert got["AVOID"]["recall"] == 0.25


# ── D11 AGENT CONFIRMATION LOOPS ────────────────────────────────────

@pg
async def test_d11_a_karen_block_ends_the_ladder_before_audrey():
    conn, tr = await P.open_tx()
    try:
        await conn.execute(
            "INSERT INTO poslearn_registrations (registration_id, kind, "
            " subject_id, version, family, role, canonical_json, document, "
            " sha256, min_sample, family_size, registered_at, status) "
            "SELECT 'M9_LOOP@1', 'MODEL', 'M9_LOOP', 1, 'T', 'CHALLENGER', "
            " d, d::jsonb, encode(sha256(convert_to(d, 'UTF8')), 'hex'), 1, "
            " 1, now(), 'ACTIVE_FORWARD' FROM (SELECT $1::text AS d) x",
            C.canonical({"subject_id": "M9_LOOP", "version": 1,
                         "kind": "MODEL", "family": "T",
                         "role": "CHALLENGER", "training_window": "N",
                         "features": [], "parameters": {},
                         "validation_method": "f", "minimum_sample": 1,
                         "promotion_threshold": {},
                         "failure_threshold": {},
                         "hypothesis_family": "T", "family_size": 1}))
        reg = (await R.registrations(conn))
        reg = [r for r in reg if r["registration_id"] == "M9_LOOP@1"][0]
        await conn.execute(
            "INSERT INTO poslearn_promotion_steps (registration_id, step, "
            " actor, outcome) VALUES ('M9_LOOP@1', 'CRITERIA_MET', "
            " 'POS_LEARN_RUNNER', 'CRITERIA_MET')")
        bad_pairs = [{"at": time.time() + i, "diff": (1.0 if i < 5 else
                                                      -0.01),
                      "sport": "s", "regime": "N"} for i in range(40)]
        got = await RUN.promotion(conn, candidates={"M9_LOOP@1": {
            "registration": reg, "verdict": {"verdict": "CRITERIA_MET"},
            "pairs": bad_pairs, "base_subject": "M0_PINNAPI_RAW"}})
        assert got["steps"] == [("M9_LOOP@1", "KAREN_CHALLENGE", "BLOCKED")]
        steps = [r["step"] for r in await conn.fetch(
            "SELECT step FROM poslearn_promotion_steps WHERE "
            " registration_id = 'M9_LOOP@1' ORDER BY step_id")]
        assert steps == ["CRITERIA_MET", "KAREN_CHALLENGE"]
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO poslearn_promotion_steps (registration_id, "
                " step, actor, outcome) VALUES ('M9_LOOP@1', "
                " 'AUDREY_EVALUATION', 'AUDREY', 'PASS')")
        await sp.rollback()
        # and a second pass does not re-challenge or skip ahead
        again = await RUN.promotion(conn, candidates={"M9_LOOP@1": {
            "registration": reg, "verdict": {"verdict": "CRITERIA_MET"},
            "pairs": bad_pairs, "base_subject": "M0_PINNAPI_RAW"}})
        assert again["steps"] == []
    finally:
        await P.close_tx(conn, tr)
